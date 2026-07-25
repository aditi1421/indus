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


MANIFEST = Path(os.getenv("NYAYA_MANIFEST", "./manifest.json")).resolve()
CACHE = Path(os.getenv("NYAYA_CACHE", "./.cache")).resolve()
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


@skill
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


@skill
def describe_source(source_id: str):
    """Show columns/dtypes/sample (tabular) or a preview (text). Call before query_table to get column names."""
    s = _src(source_id)
    if _tabular(s):
        df = _df(s)
        cols = ", ".join(f"{c} ({df[c].dtype})" for c in df.columns)
        return f"{len(df)} rows. Columns: {cols}\n{df.head(5).to_string(index=False)}"
    t = aides.to_markdown(_resolve(s))
    return f"text, {len(t)} chars\n{t[:1500]}"


@skill
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


@skill
def read_source(source_id: str, max_chars: int = 20000):
    """Read full text of a text source (md/txt/pdf/docx/html/json). Prefer query_table for tabular."""
    s = _src(source_id)
    if _tabular(s):
        return f"'{source_id}' is tabular; use query_table.\n{_df(s).head(50).to_string(index=False)}"
    t = aides.to_markdown(_resolve(s))
    return t if len(t) <= max_chars else t[:max_chars] + "\n[...truncated]"


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
    return df.to_string(index=False) or "No cases."
