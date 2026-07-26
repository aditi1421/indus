import hashlib
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pdfplumber
import requests

CACHE = Path(".cache/causelists")
CACHE.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (Macintosh) nyaya-clerk/1.0"}

# In-process negative cache: (court, date) -> time.time() of the last "not
# published" conclusion. Without this, an unpublished day re-triggers the
# full scrape + paid browser-use fallback on every call for that court/date
# (fetch()'s success cache never gets a chance to help, since there's never
# a successful fetch to cache). NEGATIVE_TTL bounds how long we trust a
# stale "not published yet" before checking again.
_NEGATIVE: dict[tuple[str, str], float] = {}
NEGATIVE_TTL = 1800


@dataclass
class Court:
    key: str
    name: str
    index_url: str
    # tokens tried against href/link text to pick that date's PDFs; {d}/{m}/{Y} filled per date
    date_patterns: list[str]


COURTS = {
    # SC: https://www.sci.gov.in/cause-list/ lists PDFs under api.sci.gov.in/jonew/cl/{Y}-{m}-{d}/...
    # e.g. href="https://api.sci.gov.in/jonew/cl/2026-07-27/M_J_1.pdf". Also has "advance/{Y}-{m}-{d}"
    # listings for upcoming days. Discovered live 2026-07-25 (Sat): weekend has no matching links
    # (confirms "not published"); Monday 2026-07-27 has many (M_J/M_C/M_S/M_R benches).
    "sc": Court("sc", "Supreme Court of India", "https://www.sci.gov.in/cause-list/",
                ["{Y}-{m}-{d}", "{d}-{m}-{Y}", "{d}.{m}.{Y}", "{d}/{m}/{Y}"]),
    # DHC: the brief's index_url (court/court_causelist) 404s. Live site nav is
    # delhihighcourt.nic.in -> /web/cause-lists -> ./cause-lists/cause-list, which resolves to
    # https://delhihighcourt.nic.in/web/cause-lists/cause-list. That page has plain <a href> PDFs
    # like "/files/2026-07/cause-list/cause_list_for_27.07.2026.pdf" (dd.mm.yyyy, some dd.mm.yy).
    "dhc": Court("dhc", "Delhi High Court", "https://delhihighcourt.nic.in/web/cause-lists/cause-list",
                 ["{d}.{m}.{Y}", "{d}.{m}.{y}", "{d}-{m}-{Y}", "{d}/{m}/{Y}"]),
    # MHC: meghalayahighcourt.nic.in/case-listing-management 404s; the real "causelist" nav item
    # just redirects to the shared eCourts India HC service (state_cd=21, dist_cd=1, court_code=1),
    # which renders its cause-list picker via JS and serves PDFs through a session-gated,
    # POST-then-signed-token flow (no static <a href=".pdf"> to regex over -- see _mhc_pdfs()).
    # index_url kept here for reference/fixture provenance; pdf_links()'s generic regex approach
    # cannot work for this court, so fetch() special-cases "mhc" via _mhc_pdfs() instead.
    "mhc": Court("mhc", "Meghalaya High Court",
                 "https://hcservices.ecourts.gov.in/ecourtindiaHC/cases/highcourt_causelist.php"
                 "?state_cd=21&dist_cd=1&court_code=1&stateNm=Meghalaya",
                 ["{d}-{m}-{Y}"]),
}

ITEM_RE = re.compile(r"^\s{0,6}(\d{1,4})[\.\)]\s+\S")
PDF_HREF_RE = re.compile(r'href="([^"]+\.pdf[^"]*)"', re.I)

# MHC / eCourts HC service constants (Meghalaya's registration in the shared eCourts platform).
# These constants (not COURTS["mhc"].index_url, which is reference/fixture-provenance only)
# are the runtime source of truth used by _mhc_pdfs() below.
_MHC_BASE = "https://hcservices.ecourts.gov.in/ecourtindiaHC/cases"
_MHC_STATE_CD, _MHC_DIST_CD, _MHC_COURT_CODE = "21", "1", "1"
_MHC_TOKEN_RE = re.compile(r"~([A-Za-z0-9%]+=*)~[YN]~\d+~\d+~")


def _tokens(court: Court, date: str) -> list[str]:
    dt = datetime.strptime(date, "%Y-%m-%d")
    return [p.format(d=f"{dt.day:02d}", m=f"{dt.month:02d}", Y=dt.year, y=f"{dt.year % 100:02d}")
            for p in court.date_patterns]


def pdf_links(court: Court, date: str) -> list[str]:
    html = requests.get(court.index_url, headers=UA, timeout=30).text
    toks = _tokens(court, date)
    matched = []
    for href in PDF_HREF_RE.findall(html):
        if any(t in href for t in toks):
            matched.append(requests.compat.urljoin(court.index_url, href))
    # SC mirrors every file under both api.sci.gov.in and webapi.sci.gov.in; take one
    # copy per path (sorted order prefers "api." over "webapi.", and webapi has been
    # observed to be the flakier of the two) so we don't double-download or hang on a
    # dead mirror.
    seen_paths = set()
    links = []
    for url in sorted(set(matched)):
        path = requests.compat.urlparse(url).path
        if path in seen_paths:
            continue
        seen_paths.add(path)
        links.append(url)
    return links


def _mhc_pdfs(date: str) -> list[bytes]:
    """Fetch MHC's cause-list PDFs for `date` via the eCourts HC-service flow.

    The public MHC site delegates cause lists to hcservices.ecourts.gov.in, whose
    picker page has no static PDF hrefs (rendered via JS). The listing is a POST to
    highcourt_causelist_qry.php that returns pipe/tilde-delimited rows, each containing
    a one-time signed token; PDFs are then fetched from display_causelist.php?filename=
    <token>, gated on the session cookie from the initial GET. No CAPTCHA is required
    for this read-only "published causelist" action. Live-verified 2026-07-27 (Mon):
    4 bench PDFs returned; 2026-07-25 (Sat) returns an empty body (not published).
    """
    listing_url = (f"{_MHC_BASE}/highcourt_causelist.php?state_cd={_MHC_STATE_CD}"
                   f"&dist_cd={_MHC_DIST_CD}&court_code={_MHC_COURT_CODE}&stateNm=Meghalaya")
    dt = datetime.strptime(date, "%Y-%m-%d")
    causelist_dt = f"{dt.day:02d}-{dt.month:02d}-{dt.year}"

    sess = requests.Session()
    sess.headers.update(UA)
    sess.get(listing_url, timeout=30)  # establishes session cookie
    resp = sess.post(
        f"{_MHC_BASE}/highcourt_causelist_qry.php",
        timeout=30,
        headers={"X-Requested-With": "XMLHttpRequest", "Referer": listing_url},
        data={
            "action_code": "pulishedCauselist",
            "causelist_dt": causelist_dt,
            "state_code": _MHC_STATE_CD,
            "dist_code": _MHC_DIST_CD,
            "court_code": _MHC_COURT_CODE,
        },
    )
    body = resp.text.strip()
    if not body or body.upper().startswith("ERROR"):
        return []

    pdfs = []
    for tok in _MHC_TOKEN_RE.findall(body):
        r = sess.get(f"{_MHC_BASE}/display_causelist.php?filename={tok}",
                     timeout=30, headers={"Referer": listing_url})
        if r.ok and r.headers.get("Content-Type", "").lower().startswith("application/pdf"):
            pdfs.append(r.content)
    return pdfs


def pdf_to_text(path: Path) -> str:
    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def _fetch_via_browser(court: str, date: str) -> str | None:
    """Last resort: an AI browser agent reads the cause list. Costs money; only on scraper miss."""
    from config import get_cfg
    from browser_use_sdk import BrowserUse
    c = COURTS[court]
    task = (f"Go to {c.index_url}. Find the daily cause list for {date} "
            f"({c.name}). Open every PDF for that date and return the FULL text content, "
            f"preserving item numbers. If none exists for that date, reply exactly NO_LIST.")
    try:
        result = BrowserUse(api_key=get_cfg().key_browser_use).run(task)
    except Exception as e:
        print(f"Browser-use fallback failed: {e}")
        return None

    text = getattr(result, "output", None)
    if not text or not text.strip():
        return None
    return None if text.strip() == "NO_LIST" else text


def fetch(court: str, date: str) -> Path:
    key = (court, date)
    cached_at = _NEGATIVE.get(key)
    if cached_at is not None and (time.time() - cached_at) < NEGATIVE_TTL:
        c = COURTS[court]
        raise ValueError(f"No cause-list found for {c.name} on {date} (cached result; "
                          f"not published as of the last check within the last "
                          f"{NEGATIVE_TTL}s).")
    try:
        out = _fetch_impl(court, date)
    except ValueError:
        _NEGATIVE[key] = time.time()
        raise
    _NEGATIVE.pop(key, None)
    return out


def _fetch_impl(court: str, date: str) -> Path:
    c = COURTS[court]
    out = CACHE / f"{court}_{date}.txt"
    if out.is_file() and out.stat().st_size > 0:
        return out

    if court == "mhc":
        pdf_blobs = _mhc_pdfs(date)
        if not pdf_blobs:
            text = _fetch_via_browser(court, date)
            if not text or not text.strip():
                raise ValueError(f"No cause-list PDFs found for {c.name} on {date} "
                                  f"(direct scrape and browser fallback both empty).")
            out.write_text(text, "utf-8")
            return out
        parts = []
        for i, data in enumerate(pdf_blobs):
            pdf = CACHE / f"{court}_{date}_{i}.pdf"
            if not pdf.is_file():
                pdf.write_bytes(data)
            parts.append(pdf_to_text(pdf))
        text = "\n\n".join(parts)
        if not text.strip():
            raise ValueError(f"Cause-list PDFs for {c.name} on {date} contained no "
                              f"extractable text (possibly scanned images).")
        out.write_text(text, "utf-8")
        return out

    links = pdf_links(c, date)
    if not links:
        text = _fetch_via_browser(court, date)
        if not text or not text.strip():
            raise ValueError(f"No cause-list PDFs found for {c.name} on {date} "
                              f"(direct scrape and browser fallback both empty).")
        out.write_text(text, "utf-8")
        return out
    parts = []
    for url in links:
        pdf = CACHE / f"{court}_{date}_{hashlib.md5(url.encode()).hexdigest()[:8]}.pdf"
        if not pdf.is_file():
            # DHC's combined "Sitting of Benches" cause list can run 15-20MB; government
            # hosts are slow and occasionally drop the connection mid-transfer, so give
            # downloads a generous timeout and one retry before giving up on that file.
            last_exc = None
            for attempt in range(2):
                try:
                    r = requests.get(url, headers=UA, timeout=120)
                    r.raise_for_status()
                    pdf.write_bytes(r.content)
                    last_exc = None
                    break
                except requests.exceptions.RequestException as exc:
                    last_exc = exc
            if last_exc is not None:
                raise last_exc
        parts.append(pdf_to_text(pdf))
    text = "\n\n".join(parts)
    if not text.strip():
        raise ValueError(f"Cause-list PDFs for {c.name} on {date} contained no "
                          f"extractable text (possibly scanned images).")
    out.write_text(text, "utf-8")
    return out


def split_items(text: str) -> list[str]:
    items, cur = [], []
    for line in text.splitlines():
        if ITEM_RE.match(line):
            if cur:
                items.append("\n".join(cur).strip())
            cur = [line]
        elif cur:
            cur.append(line)
    if cur:
        items.append("\n".join(cur).strip())
    return [i for i in items if i]


def normalize_case_no(s: str) -> str:
    return re.sub(r"[^A-Z0-9/]", "", s.upper())


def search(court: str, date: str, query: str) -> list[str]:
    text = fetch(court, date).read_text("utf-8")
    q_norm = normalize_case_no(query)
    q_low = query.lower()
    hits = []
    for item in split_items(text):
        if (q_norm and q_norm in normalize_case_no(item)) or q_low in item.lower():
            hits.append(item)
    return hits
