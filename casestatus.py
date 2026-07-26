"""Supreme Court of India case-status lookup over the public sci.gov.in AJAX API.

Reverse-engineered flow (see .superpowers/sdd/sc-casestatus-investigation.md):
form page -> fresh arithmetic captcha (sets PHPSESSID) -> search POST -> optional
captcha-free drill-down. These are undocumented NIC endpoints; every parse fails
loud with a clear message rather than a traceback when the shape changes.
"""

import base64
import json
import random
import re
import string
import time

import openai
import requests
from bs4 import BeautifulSoup

from config import get_cfg

BASE = "https://www.sci.gov.in"
AJAX = f"{BASE}/wp-admin/admin-ajax.php"
CASE_NO_PAGE = "/case-status-case-no/"
DIARY_NO_PAGE = "/case-status-diary-no/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
TIMEOUT = 30
RETRY_SLEEP = 1.5  # politeness delay between captcha retries

CAPTCHA_PROMPT = (
    "This image is a simple arithmetic captcha like 'N + M' or 'N - M' "
    "(single digits, possibly rotated). Reply with ONLY the integer result "
    "of the calculation. No words, no punctuation, no explanation - just the integer."
)


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"})
    return s


def _load_form_tokens(session, page_path) -> dict:
    """GET the form page and harvest the server-rendered hidden fields:
    the dynamic tok_<40hex> name/value pair, sci_form_nonce, _form_time,
    _form_signature and _wp_http_referer. All plain HTML — no JS needed."""
    r = session.get(BASE + page_path, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    tokens, tok_pair = {}, None
    for inp in soup.find_all("input"):
        name = inp.get("name") or ""
        value = inp.get("value") or ""
        if name.startswith("tok_"):
            tok_pair = (name, value)
        elif name in ("sci_form_nonce", "_form_time", "_form_signature", "_wp_http_referer"):
            tokens[name] = value
    missing = [k for k in ("sci_form_nonce", "_form_time", "_form_signature") if k not in tokens]
    if tok_pair is None:
        missing.append("tok_*")
    if missing:
        raise ValueError(
            f"SC form page {page_path} is missing expected hidden field(s) "
            f"{missing} — the site's form layout may have changed")
    tokens.setdefault("_wp_http_referer", page_path)
    tokens[tok_pair[0]] = tok_pair[1]
    return tokens


def _fetch_captcha(session, page_path):
    """Mint a fresh captcha id (like the page JS does), GET the PNG.
    This GET sets PHPSESSID — the cookie that ties the answer to the search POST."""
    cid = "".join(random.choices(string.ascii_lowercase + string.digits, k=40))
    r = session.get(f"{BASE}/?_siwp_captcha&id={cid}",
                    headers={"Referer": BASE + page_path}, timeout=TIMEOUT)
    r.raise_for_status()
    if not r.content.startswith(b"\x89PNG"):
        raise ValueError("SC captcha endpoint did not return a PNG image — "
                         "the captcha mechanism may have changed")
    return cid, r.content


def default_solver(png: bytes) -> int:
    """Solve the arithmetic captcha with OpenAI vision (gpt-4.1-mini).
    Raises ValueError on an unparseable reply so the retry loop fetches a fresh one."""
    client = openai.OpenAI(api_key=get_cfg().key_openai)
    b64 = base64.b64encode(png).decode()
    r = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": [
            {"type": "text", "text": CAPTCHA_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]}])
    text = (r.choices[0].message.content or "").strip().rstrip(".")
    return int(text)  # ValueError -> caller retries with a fresh captcha


def _parse_results(results_html) -> list[dict]:
    """Parse the success resultsHtml table into row dicts."""
    soup = BeautifulSoup(results_html, "lxml")
    trs = soup.find_all("tr")
    if not trs:
        raise ValueError("SC search succeeded but resultsHtml contained no table rows — "
                         "the results markup may have changed")
    # a <tr> with <td> cells is a data row; header rows carry only <th>
    data_trs = [tr for tr in trs if tr.find("td")]
    rows = []
    for tr in data_trs:
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue  # spacer / malformed rows
        texts = [td.get_text(" ", strip=True) for td in tds]
        serial, diary_txt, case_txt, petitioner, respondent, status = texts[:6]

        holder = tr if tr.has_attr("data-diary-no") else tr.find(attrs={"data-diary-no": True})
        yholder = tr if tr.has_attr("data-diary-year") else tr.find(attrs={"data-diary-year": True})
        diary_no = str(holder.get("data-diary-no")) if holder else ""
        diary_year = str(yholder.get("data-diary-year")) if yholder else ""
        if not (diary_no and diary_year):
            m = re.match(r"\s*(\d+)\s*/\s*(\d{4})", diary_txt)
            if m:
                diary_no, diary_year = m.group(1), m.group(2)

        case_number, registered_on = case_txt, ""
        m = re.search(r"(?i)registered\s+on[:\s]*([0-9]{1,2}-[0-9]{1,2}-[0-9]{4})", case_txt)
        if m:
            registered_on = m.group(1)
            case_number = case_txt[:m.start()].strip()

        rows.append({"serial": serial, "diary_no": diary_no, "diary_year": diary_year,
                     "case_number": case_number, "registered_on": registered_on,
                     "petitioner": petitioner, "respondent": respondent, "status": status})
    if data_trs and not rows:
        raise ValueError("resultsHtml rows did not match the expected 6-column layout — "
                         "the SC site may have changed")
    return rows


def _decode_failure_message(data) -> str:
    """On success:false, `data` is a double-encoded JSON string like
    '{"message": "The captcha code entered was incorrect."}'."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return data
    if isinstance(data, dict):
        return str(data.get("message", ""))
    return str(data)


def _run_search(action, page_path, form_fields, *, max_captcha_retries, solve) -> dict:
    if solve is None:
        solve = default_solver
    session = _new_session()
    for attempt in range(max_captcha_retries):
        if attempt:
            time.sleep(RETRY_SLEEP)
        try:
            tokens = _load_form_tokens(session, page_path)  # re-fetch: keep tokens fresh
            cid, png = _fetch_captcha(session, page_path)
        except (ValueError, requests.RequestException) as e:
            return {"found": False, "results": [],
                    "error": f"SC case-status fetch failed: {e}"}
        try:
            answer = solve(png)
        except ValueError:
            continue  # solver couldn't read it — burn the attempt, fetch a fresh captcha
        except openai.OpenAIError as e:
            # a dead API won't recover mid-loop — fail immediately, no retry
            return {"found": False, "results": [],
                    "error": f"captcha solver unavailable: {type(e).__name__}"}

        # **tokens first so a stray harvested "scid" field can't clobber the fresh cid
        payload = {"action": action, "language": "en", **form_fields,
                   **tokens, "siwp_captcha_value": str(answer),
                   "scid": cid, "es_ajax_request": "1"}
        try:
            resp = session.post(
                AJAX, data=payload, timeout=TIMEOUT,
                headers={"Referer": BASE + page_path, "Origin": BASE,
                         "X-Requested-With": "XMLHttpRequest"})
        except requests.RequestException as e:
            return {"found": False, "results": [],
                    "error": f"SC case-status search request failed: {e}"}
        if resp.status_code != 200:
            return {"found": False, "results": [],
                    "error": f"SC portal returned HTTP {resp.status_code}"}
        try:
            body = resp.json()
        except ValueError:
            return {"found": False, "results": [],
                    "error": "SC case-status endpoint returned non-JSON — "
                             "the API may have changed"}
        if not isinstance(body, dict):
            return {"found": False, "results": [],
                    "error": "SC case-status endpoint returned an unrecognized JSON shape — "
                             "the API may have changed"}

        if body.get("success") is True:
            data = body.get("data")
            html = data.get("resultsHtml", "") if isinstance(data, dict) else ""
            if not html:
                return {"found": False, "results": [],
                        "error": "SC search succeeded but returned no resultsHtml — "
                                 "the response shape may have changed"}
            try:
                results = _parse_results(html)
            except ValueError as e:
                return {"found": False, "results": [], "error": str(e)}
            return {"found": bool(results), "results": results, "error": None}

        if "success" not in body:
            return {"found": False, "results": [],
                    "error": "SC case-status endpoint returned an unrecognized JSON shape — "
                             "the API may have changed"}
        message = _decode_failure_message(body.get("data"))
        low = message.lower()
        if "captcha" in low:
            continue  # wrong answer — fresh captcha, retry
        if any(p in low for p in ("nothing found", "no record", "not found", "no case")):
            # recognized not-found wording: the search ran cleanly and matched nothing
            return {"found": True, "results": [], "error": None}
        # anything else is a server-side rejection (nonce/token/wording change) — say so
        return {"found": False, "results": [],
                "error": f"SC search rejected: {message}"}

    return {"found": False, "results": [],
            "error": f"could not solve captcha after {max_captcha_retries} tries"}


def sc_case_status(case_type: int, number: int, year: int, *,
                   max_captcha_retries: int = 5, solve=None) -> dict:
    """Search SC by Case Number (action=get_case_status_case_no).
    Returns {"found": bool, "results": [{serial, diary_no, diary_year, case_number,
    registered_on, petitioner, respondent, status}...], "error": str|None}."""
    return _run_search(
        "get_case_status_case_no", CASE_NO_PAGE,
        {"case_type": str(case_type), "case_no": str(number), "year": str(year)},
        max_captcha_retries=max_captcha_retries, solve=solve)


def sc_diary_status(diary_no: int, year: int, *,
                    max_captcha_retries: int = 5, solve=None) -> dict:
    """Search SC by Diary Number (action=get_case_status_diary_no). Same return shape."""
    return _run_search(
        "get_case_status_diary_no", DIARY_NO_PAGE,
        {"diary_no": str(diary_no), "year": str(year)},
        max_captcha_retries=max_captcha_retries, solve=solve)


# --- drill-down (no captcha) ---

_norm = lambda s: re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


def _split_lines(value) -> list[str]:
    """Split a party-list cell into entries, dropping serial prefixes ('1 ', '1.1 ')."""
    parts = [p.strip() for p in re.split(r"\n|\s*\d+\s*\.\s+", value) if p.strip()]
    return [re.sub(r"^\d+(?:\.\d+)*\s+", "", p) for p in parts]


def _parse_detail(html) -> dict:
    soup = BeautifulSoup(html or "", "lxml")
    pairs = []
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) >= 2:
            label = cells[0].get_text(" ", strip=True)
            value = "\n".join(c.get_text("\n", strip=True) for c in cells[1:]).strip()
            if label and value:
                pairs.append((label, value))
    if not pairs:
        raise ValueError("SC case-details response had no recognizable label/value rows — "
                         "the detail markup may have changed")

    def find_raw(*aliases):
        for label, value in pairs:
            n = _norm(label)
            if any(a in n for a in aliases):
                return value
        return ""

    def find(*aliases):  # single-line fields: collapse internal newlines
        return find_raw(*aliases).replace("\n", " ").strip()

    last_listed_raw = find("last listed", "listed on")
    m = re.search(r"\[(.+?)\]", last_listed_raw)
    bench_src = (m.group(1) if m else find("coram", "bench")).strip()
    if "HON'BLE" in bench_src:
        # the live markup glues names to a lowercase "and" (e.g. "KOHLIand HON'BLE...")
        bench = [re.sub(r"\s*and\s*$", "", p).strip(" ,")
                 for p in re.split(r"(?=HON'BLE)", bench_src) if p.strip()]
    else:
        bench = [b.strip() for b in re.split(r",|\band\b", bench_src) if b.strip()]

    case_number = find("case number", "case no")
    registered_on = find("registered on", "registration")
    if not registered_on:
        m = re.search(r"(?i)registered\s+on[:\s]*([0-9]{1,2}-[0-9]{1,2}-[0-9]{4})", case_number)
        if m:
            registered_on = m.group(1)
            case_number = case_number[:m.start()].strip()

    # live markup embeds filing date + section inside the "Diary Number" row
    diary_raw = find("diary")
    filed_on = find("filed on", "filing date")
    if not filed_on:
        m = re.search(r"(?i)filed\s+on[:\s]*([0-9]{1,2}-[0-9]{1,2}-[0-9]{4})", diary_raw)
        filed_on = m.group(1) if m else ""
    section = find("section")
    if not section:
        m = re.search(r"(?i)section\s*:\s*([^\]\n]+)", diary_raw)
        section = m.group(1).strip() if m else ""

    return {
        "cnr": find("cnr"),
        "filed_on": filed_on,
        "section": section,
        "case_number": case_number,
        "registered_on": registered_on,
        "last_listed_on": re.sub(r"\[.*?\]", "", last_listed_raw).strip(),
        "bench": bench,
        "status_stage": find("stage", "status"),
        "disposal": find("disposal", "disposed", "disp type", "disp"),
        "category": find("category"),
        "petitioners": _split_lines(find_raw("petitioner")),
        "respondents": _split_lines(find_raw("respondent")),
        "advocates": {label: value.replace("\n", " ").strip()
                      for label, value in pairs if "advocate" in _norm(label)},
        "raw_html": html,
    }


def sc_case_details(diary_no: int, diary_year: int) -> dict:
    """Full case record drill-down (action=get_case_details) — no captcha needed.
    Returns {cnr, filed_on, section, case_number, registered_on, last_listed_on,
    bench, status_stage, disposal, category, petitioners, respondents, advocates, raw_html}."""
    session = _new_session()
    try:
        r = session.get(
            AJAX, timeout=TIMEOUT,
            params={"action": "get_case_details", "diary_no": diary_no,
                    "diary_year": diary_year, "tab_name": "",
                    "es_ajax_request": "1", "language": "en"},
            headers={"Referer": BASE + CASE_NO_PAGE, "X-Requested-With": "XMLHttpRequest"})
        r.raise_for_status()
    except requests.RequestException as e:
        raise ValueError(f"SC case-details request failed: {e}")
    try:
        body = r.json()
    except ValueError:
        raise ValueError("SC case-details endpoint returned non-JSON — the API may have changed")
    if body.get("success") is not True or not isinstance(body.get("data"), str):
        raise ValueError(
            f"SC case-details lookup failed for diary {diary_no}/{diary_year} — "
            "no record or the API shape changed")
    return _parse_detail(body["data"])
