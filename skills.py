import os, json
from pathlib import Path

import requests
import pandas as pd
from agents import function_tool
import aides

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 240)

SKILLS = []


def skill(fn):
    SKILLS.append(function_tool(failure_error_function=lambda c, e: str(e))(fn))
    return fn


MANIFEST = Path(os.getenv("INDUS_MANIFEST", "./manifest.json")).resolve()
CACHE = Path(os.getenv("INDUS_CACHE", "./.cache")).resolve()
CACHE.mkdir(parents=True, exist_ok=True)
TABULAR = {"csv", "tsv", "xlsx", "xls"}


def _sources():
    if not MANIFEST.is_file():
        raise ValueError(f"Manifest not found at {MANIFEST}.")
    return {s["id"]: s for s in json.loads(MANIFEST.read_text("utf-8"))["sources"]}


def _src(sid):
    m = _sources()
    if sid not in m:
        raise ValueError(
            f"Unknown source '{sid}'. Known: {', '.join(sorted(m)) or '(none)'}."
        )
    return m[sid]


def _tabular(s):
    return (s.get("format") or "").lower() in TABULAR


def _resolve(s):
    if s.get("type") == "url":
        dest = CACHE / f"{s['id']}.{(s.get('format') or 'bin').lower()}"
        if not dest.is_file():
            r = requests.get(s["location"], timeout=30)
            r.raise_for_status()
            dest.write_bytes(r.content)
        return dest
    p = Path(s["location"]).expanduser().resolve()
    if not p.is_file():
        raise ValueError(f"Local source '{s['id']}' not found at {p}.")
    return p


def _df(s):
    p, fmt = _resolve(s), (s.get("format") or "csv").lower()
    if fmt == "csv":
        return pd.read_csv(p)
    if fmt == "tsv":
        return pd.read_csv(p, sep="\t")
    if fmt in ("xlsx", "xls"):
        return pd.read_excel(p, sheet_name=s.get("sheet", 0))
    raise ValueError(f"'{s['id']}' is not tabular.")


# These four depend on ./manifest.json, which is a local-dev fixture that
# won't exist on EC2 in production. Registering them unconditionally would
# leave permanently-broken tools polluting the agent's tool surface (every
# call fails with "Manifest not found"). Register them as skills only when
# the manifest is actually present at import time; otherwise they're just
# plain, unregistered functions.


def list_sources(tag: str = ""):
    """List allowed sources from the manifest, optionally filtered by tag. Call first to pick a source id."""
    out = []
    for sid, s in sorted(_sources().items()):
        if tag and tag.lower() not in [t.lower() for t in s.get("tags", [])]:
            continue
        out.append(
            f"{sid} | {s.get('title', '')} | {'tabular' if _tabular(s) else 'text'} | "
            f"tags: {', '.join(s.get('tags', []))} | {s.get('description', '')}"
        )
    return "\n".join(out) or "No sources."


def describe_source(source_id: str):
    """Show columns/dtypes/sample (tabular) or a preview (text). Call before query_table to get column names."""
    s = _src(source_id)
    if _tabular(s):
        df = _df(s)
        cols = ", ".join(f"{c} ({df[c].dtype})" for c in df.columns)
        return f"{len(df)} rows. Columns: {cols}\n{df.head(5).to_string(index=False)}"
    t = aides.to_markdown(_resolve(s))
    return f"text, {len(t)} chars\n{t[:1500]}"


def query_table(source_id: str, where: str = "", columns: str = "", limit: int = 50):
    """Query a tabular source with pandas. where: a .query() expr e.g. "Court=='MHC'"; backtick columns with spaces."""
    s = _src(source_id)
    if not _tabular(s):
        raise ValueError(f"'{source_id}' is text; use read_source.")
    df = _df(s)
    if where.strip():
        try:
            df = df.query(where, engine="python")
        except Exception as e:
            raise ValueError(f"Bad where: {e}. Columns: {list(df.columns)}")
    if columns.strip():
        cols = [c.strip() for c in columns.split(",") if c.strip()]
        bad = [c for c in cols if c not in df.columns]
        if bad:
            raise ValueError(f"Unknown columns {bad}. Available: {list(df.columns)}")
        df = df[cols]
    total = len(df)
    df = df.head(limit) if limit and total > limit else df
    return f"{len(df)}/{total} rows from '{source_id}':\n{df.to_string(index=False) if total else '(none)'}"


def read_source(source_id: str, max_chars: int = 20000):
    """Read full text of a text source (md/txt/pdf/docx/html/json). Prefer query_table for tabular."""
    s = _src(source_id)
    if _tabular(s):
        return f"'{source_id}' is tabular; use query_table.\n{_df(s).head(50).to_string(index=False)}"
    t = aides.to_markdown(_resolve(s))
    return t if len(t) <= max_chars else t[:max_chars] + "\n[...truncated]"


if MANIFEST.is_file():
    for _fn in (list_sources, describe_source, query_table, read_source):
        skill(_fn)


@skill
def current_datetime(tz: str = "Asia/Kolkata"):
    """Current date/time. Use for any question involving today/tomorrow/this week/deadlines."""
    ts = aides.now(tz=tz)
    return f"{aides.dtts(ts, tz=tz)} ({ts.strftime('%A')}), {tz}"


# --- court clerk skills ---


@skill
def search_causelist(court: str, date: str, query: str):
    """Search a court's cause list. court: sc|dhc|mhc. date: YYYY-MM-DD. query: case number or party name."""
    import causelists
    court = court.strip().lower()
    if court not in causelists.COURTS:
        raise ValueError(f"Unknown court '{court}'. Use: sc, dhc, mhc.")
    hits = causelists.search(court, date, query)
    name = causelists.COURTS[court].name
    if not hits:
        return f"No match for '{query}' in {name} cause list of {date}."
    return f"{len(hits)} match(es) in {name} list of {date}:\n\n" + "\n\n".join(hits[:20])


@skill
def todays_causelist_matches(date: str):
    """Firm matters listed on a date (YYYY-MM-DD) across SC, Delhi HC, Meghalaya HC. Use for 'what's listed today/tomorrow'."""
    import cases
    rows = cases.listings_for(date)
    if not rows:
        return f"No firm matters found in published cause lists for {date}."
    lines = []
    for r in rows:
        lines.append(f"• {r['case_no']} ({r['client']}) — {r['court'].upper()}\n{r['matches'][0]}")
    return f"{len(rows)} firm matter(s) listed on {date}:\n\n" + "\n\n".join(lines)


@skill
def list_firm_cases(court: str = ""):
    """List the firm's tracked cases, optionally filtered by court (sc|dhc|mhc)."""
    import cases
    df = cases.firm_cases()
    if court:
        df = df[df.court == court.lower()]
    if df.empty:
        return "No cases."
    return df.to_string(index=False)


@skill
def firm_register():
    """The firm's full file register (FILE, DEPARTMENT, RECEIPT DATE, REMARKS,
    ASSIGNED, STATUS -- every column, unfiltered). Use for questions about the
    firm's file register: pending files, status, assignments, receipt dates."""
    import cases
    df = cases.raw_register()
    total = len(df)
    note = ""
    if total > 100:
        df = df.head(100)
        note = f"\n(showing first 100 of {total} rows)"
    return df.to_string(index=False) + note


# --- supreme court case-status skills ---


def _sc_format(res: dict, what: str) -> str:
    """Readable, WhatsApp-friendly summary of a sc_case_status/sc_diary_status result."""
    import casestatus
    if res.get("error"):
        return f"SC lookup failed for {what}: {res['error']}"
    results = res.get("results") or []
    if not res.get("found") or not results:
        return f"No Supreme Court record found for {what}."
    lines = [f"Supreme Court — {len(results)} result(s) for {what}:"]
    shown = results[:15]
    for r in shown:
        lines.append(
            f"\n{r['case_number']}"
            + (f" (registered {r['registered_on']})" if r.get("registered_on") else "")
            + f"\nDiary: {r['diary_no']}/{r['diary_year']}"
            f"\n{r['petitioner']} vs {r['respondent']}"
            f"\nStatus: {r['status']}")
    if len(results) > len(shown):
        lines.append(f"\n…and {len(results) - len(shown)} more")
    if len(results) == 1:
        r = results[0]
        try:
            d = casestatus.sc_case_details(int(r["diary_no"]), int(r["diary_year"]))
            extra = []
            if d.get("cnr"):
                extra.append(f"CNR: {d['cnr']}")
            if d.get("bench"):
                extra.append("Bench: " + ", ".join(d["bench"]))
            if d.get("last_listed_on"):
                extra.append(f"Last listed: {d['last_listed_on']}")
            if d.get("status_stage"):
                extra.append(f"Stage: {d['status_stage']}")
            if d.get("disposal"):
                extra.append(f"Disposal: {d['disposal']}")
            if extra:
                lines.append("\n" + "\n".join(extra))
        except (ValueError, TypeError) as e:
            lines.append(f"\n(Full details unavailable: {e})")
    return "\n".join(lines)


@skill
def sc_case_status_lookup(case_type_code: int, case_number: int, year: int):
    """Supreme Court of India case status by case number. Common case_type_code values:
    1=SLP(C), 2=SLP(Crl), 3=Civil Appeal, 4=Criminal Appeal, 5=W.P.(C), 6=W.P.(Crl),
    9=Review Petition (Civil). If the user implies the current year (e.g. 'this year'
    or no year given), call current_datetime first to resolve it. Slow: solves a captcha."""
    import casestatus
    res = casestatus.sc_case_status(case_type_code, case_number, year)
    return _sc_format(res, f"case {case_number}/{year} (type {case_type_code})")


@skill
def sc_diary_status_lookup(diary_number: int, year: int):
    """Supreme Court of India case status by diary number (e.g. Diary 52650/2023).
    If the user implies the current year, call current_datetime first to resolve it.
    Slow: solves a captcha."""
    import casestatus
    res = casestatus.sc_diary_status(diary_number, year)
    return _sc_format(res, f"diary {diary_number}/{year}")


# --- billing skills (Zoho Invoice) ---


def _zoho():
    from zoho import Zoho
    return Zoho.from_cfg()


@skill
def zoho_find_customer(name: str):
    """Find a Zoho customer by (partial) name. Returns customer ids for invoicing."""
    hits = _zoho().customers(name)
    if not hits:
        return f"No Zoho customer matching '{name}'."
    return "\n".join(f"{c['contact_id']} | {c['contact_name']}" for c in hits[:10])


@skill
def zoho_create_draft_invoice(customer_id: str, item_description: str, amount: float, quantity: float = 1):
    """Create a DRAFT invoice in Zoho. It is NOT sent to anyone. Report invoice number, total and invoice_id back; the firm reviews and finalizes drafts in the Zoho dashboard."""
    inv = _zoho().create_draft(customer_id, [{"name": item_description, "rate": amount, "quantity": quantity}])
    total = inv.get("total")
    total_txt = f"₹{total:.2f}" if isinstance(total, (int, float)) else "amount unavailable"
    return (f"Draft created: {inv.get('invoice_number', '?')} for {total_txt} "
            f"(invoice_id={inv.get('invoice_id', '?')}, status={inv.get('status', 'draft')}). "
            f"NOT sent — review it in the Zoho dashboard.")
