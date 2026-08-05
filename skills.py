import functools
import os, json
import re
from pathlib import Path

import requests
import pandas as pd
from agents import function_tool
import aides

import billing
import notes
import provenance

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 240)

SKILLS = []


def _cite(text, source):
    """Attach the single source tag the persona promises on every grounded answer,
    and record it so 'where did that come from' still works after the answer has
    scrolled out of the conversation history.

    Only call this when something was actually retrieved. A miss has no source.
    """
    provenance.record(source)
    return f"{text}\n[source: {source}]"


# Tool results are fed straight back into the model, so their size is a running
# cost on every turn of the agent loop, not just on what server.py persists.
# firm_register can return 100 spreadsheet rows in one result; unbounded, that
# is the case where a single question quietly costs more than a hundred normal
# ones. 4,000 characters is roughly 1,000 tokens: enough for a cause list or a
# billing profile, and a hard ceiling on the pathological case.
MAX_TOOL_RESULT_CHARS = 4000
_CAP_NOTE = ("\n[truncated at {n} characters — narrow the query "
             "(add a filter, a date, or a name) to see more]")
_SOURCE_TAG = re.compile(r"\n\[source: [^\]]*\]\s*$")


def _cap(text, limit=MAX_TOOL_RESULT_CHARS):
    """Bound one tool result, keeping its source tag.

    The citation is appended last, so a naive truncation would cut it off and
    the answer would silently lose its provenance — which is the one thing the
    persona promises. Trim the body instead and re-attach the tag.
    """
    if not isinstance(text, str) or len(text) <= limit:
        return text
    match = _SOURCE_TAG.search(text)
    source = match.group(0) if match else ""
    body = text[:match.start()] if match else text
    note = _CAP_NOTE.format(n=limit)
    keep = max(0, limit - len(source) - len(note))
    return body[:keep] + note + source


def skill(fn):
    @functools.wraps(fn)
    def capped(*args, **kwargs):
        return _cap(fn(*args, **kwargs))

    SKILLS.append(function_tool(failure_error_function=lambda c, e: str(e))(capped))
    return capped


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


# --- taught notes ---


@skill
def remember_note(text: str, taught_by: str = ""):
    """Permanently remember a fact the firm tells you, e.g. 'MeECL means Meghalaya
    Energy Corporation Limited'. Call this ONLY when someone explicitly asks you to
    remember something. taught_by is the name of the person who said it."""
    n = notes.add(text.strip(), added_by=taught_by.strip())
    return f"Noted [{n['id']}]: {n['text']}"


@skill
def list_notes():
    """Everything the firm has taught you, with who taught it and when."""
    items = notes.load()
    if not items:
        return "Nothing has been taught to me yet."
    return "\n".join(
        f"[{n['id']}] {n['text']}"
        + (f" (taught by {n['added_by']}" + f", {n['added_at'][:10]})" if n.get("added_by")
           else f" ({n.get('added_at', '')[:10]})")
        for n in items)


@skill
def forget_note(note_id: str):
    """Remove one taught note by its id. Use list_notes first to find the id."""
    if notes.remove(note_id):
        return f"Forgot note [{note_id}]."
    return f"There is no note [{note_id}]."


@skill
def recent_sources(limit: int = 5):
    """Where your earlier answers in this group came from. Use this when someone asks
    where you got something, especially about an answer older than the current
    conversation."""
    entries = provenance.recent(provenance.current_chat(), limit=limit)
    if not entries:
        return "No recorded sources yet."
    out = []
    for e in entries:
        srcs = ", ".join(e.get("sources") or []) or "(nothing retrieved)"
        out.append(f"{e.get('ts', '')[:16]} | {e.get('question', '')[:70]} | {srcs}")
    return "\n".join(out)


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
    # A specific lookup gets the full item (court number and listing time live
    # in the detail lines); a broad one gets one line per match so every match
    # survives the tool-result cap instead of the first few crowding out the rest.
    if len(hits) <= 3:
        shown = "\n\n".join(hits)
    else:
        shown = "\n".join(causelists.summarize_item(h, around=query) for h in hits)
    return _cite(f"{len(hits)} match(es) in {name} list of {date}:\n\n" + shown,
                 f"{name} cause list, {date}")


@skill
def todays_causelist_matches(date: str):
    """Firm matters listed on a date (YYYY-MM-DD) across SC, Delhi HC, Meghalaya HC.
    Use for "what's listed today/tomorrow". States explicitly when a court's list could
    not be read, which is NOT the same as nothing being listed."""
    import cases
    import causelists
    result = cases.listings_for(date)
    rows, checked, unavailable = result["rows"], result["checked"], result["unavailable"]
    names = {"sc": "Supreme Court", "dhc": "Delhi High Court", "mhc": "Meghalaya High Court"}

    def label(courts):
        return ", ".join(names.get(c, c) for c in courts)

    # Nothing could be read, so nothing can be claimed. Saying "no matters
    # listed" here is the failure that costs someone a hearing.
    if not checked:
        return (f"Could not check {date}: no cause list was available for "
                f"{label(unavailable) or 'any court'}. That is not the same as nothing "
                f"being listed — the lists themselves could not be read.")

    if rows:
        # One line per matched item, never a raw extract: a single In re item's
        # counsel lines can spend the whole tool-result cap, and showing only
        # matches[0] hid every further matter a name token found (the bug that
        # reported 3 matters on a 12-matter day, 2026-08-04). The headline
        # counts listings, not search tokens -- "3 firm matters" on a 12-listing
        # day was the number the model repeated. A block the extractor smeared
        # across several searches is shown once, under the first that hit it.
        lines, shown, total = [], set(), 0
        for r in rows:
            who = f" ({r['parties']})" if r.get("parties") else ""
            fresh = [m for m in r["matches"] if m not in shown]
            shown.update(fresh)
            total += len(fresh)
            note = ("" if len(fresh) == len(r["matches"])
                    else f" ({len(r['matches']) - len(fresh)} shown above)")
            lines.append(f"• {r['token']}{who} — {r['court'].upper()}, "
                         f"{len(fresh)} item(s){note}:")
            lines.extend(f"  {causelists.summarize_item(m, around=r['token'])}"
                         for m in fresh)
        body = (f"{total} listing(s) for the firm on {date} "
                f"across {len(rows)} matched search(es):\n\n" + "\n".join(lines))
    else:
        body = (f"No firm matters in the cause lists I could read for {date} "
                f"({label(checked)}).")

    if unavailable:
        body += (f"\n\nCould not check {label(unavailable)}: no list was available "
                 f"for {date}.")
    return _cite(body, f"cause lists for {label(checked)}, {date}")


@skill
def firm_sc_matters():
    """The firm's PENDING Supreme Court matters, from the Supreme Court registry itself
    (searched by the firm's Advocate-on-Record code). Use for "what Supreme Court cases
    do we have". Slow on the first call of the day: it solves a captcha."""
    import cases
    rows = cases._sc_aor_matters()
    if not rows:
        return "No pending Supreme Court matters found for the firm's AOR code."
    lines = [f"{len(rows)} pending Supreme Court matter(s):"]
    for r in rows[:25]:
        lines.append(f"• {r['case_no']} — {r['parties']}")
    if len(rows) > 25:
        lines.append(f"…and {len(rows) - 25} more")
    return _cite("\n".join(lines), "sci.gov.in case status by AOR code")


@skill
def list_firm_cases(court: str = ""):
    """List the firm's tracked cases, optionally filtered by court (sc|dhc|mhc)."""
    import cases
    df = cases.firm_cases()
    if court:
        df = df[df.court == court.lower()]
    if df.empty:
        return "No cases."
    return _cite(df.to_string(index=False), "firm case list")


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
    return _cite(df.to_string(index=False) + note, f"firm file register, {total} rows")


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
    return _cite("\n".join(lines), f"sci.gov.in case status, {what}")


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


@skill
def mhc_case_status_lookup(case_type: str, number: int, year: int):
    """Meghalaya High Court case status by case number. case_type is the court's own
    code as a literal string, e.g. 'WP(C)', 'CRP', 'Cont.Cas(C)', 'AB', 'BA'. Returns
    the parties and whether the matter is pending or disposed. Fast: no captcha cost."""
    import mhcstatus
    res = mhcstatus.mhc_case_status(case_type, number, year)
    what = f"{case_type} {number}/{year}"
    if res.get("error"):
        return f"Meghalaya HC lookup failed for {what}: {res['error']}"
    rows = res.get("results") or []
    if not rows:
        return f"No Meghalaya High Court record found for {what}."
    lines = [f"Meghalaya High Court — {len(rows)} result(s) for {what}:"]
    for r in rows[:10]:
        lines.append(f"\n{r['case_no']}\n{r['petitioner']} vs {r['respondent']}"
                     f"\nStatus: {r['status']}")
    if len(rows) > 10:
        lines.append(f"\n…and {len(rows) - 10} more")
    return _cite("\n".join(lines), f"meghalayahighcourt.nic.in case status, {what}")


@skill
def legal_research(query: str):
    """Search legal news and judgment sites (Indian Kanoon, LiveLaw, Bar and Bench,
    SC Observer, court sites) for judgments, orders and commentary. For research
    questions only — cause lists, case status and billing have their own tools,
    which are authoritative. Results are leads to read, not verified court records."""
    import research
    res = research.search(query)
    if res.get("error"):
        return f"Legal research search failed: {res['error']}"
    rows = res.get("results") or []
    if not rows:
        return (f"No results on the firm's legal sources for '{query}'. "
                f"Rephrasing with party names or the statute may help.")
    lines = [f"{len(rows)} result(s) for '{query}':"]
    for i, r in enumerate(rows, 1):
        when = f" ({r['published']})" if r["published"] else ""
        lines.append(f"\n{i}. {r['title']}{when}")
        if r["highlight"]:
            lines.append(f"   {r['highlight']}")
        lines.append(f"   {r['url']}")
    return _cite("\n".join(lines), "Exa legal web search")


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
    return _cite("\n".join(f"{c['contact_id']} | {c['contact_name']}" for c in hits[:10]),
                 "Zoho Invoice, contacts")


@skill
def zoho_recent_invoices(customer_id: str, limit: int = 2):
    """A customer's recent invoices with their line items, rates and wording. Call this
    BEFORE raising a new invoice so the draft matches the firm's established format and
    that customer's standing rate. Also answers 'what did we last charge X'."""
    z = _zoho()
    recent = z.invoices(customer_id=customer_id, limit=limit)
    if not recent:
        return f"No invoice history for customer {customer_id}."
    out = []
    for inv in recent:
        full = z.invoice(inv["invoice_id"])
        total = full.get("total")
        total_txt = f"₹{total:.2f}" if isinstance(total, (int, float)) else "?"
        head = (f"{full.get('invoice_number', '?')} | {full.get('date', '?')} | "
                f"{full.get('status', '?')} | total {total_txt}")
        lines = [f"  • {li.get('name', '')} | {li.get('description', '')} | "
                 f"rate {li.get('rate')} x {li.get('quantity')}"
                 for li in full.get("line_items", [])]
        out.append("\n".join([head] + lines))
    numbers = ", ".join(i.get("invoice_number", "?") for i in recent)
    return _cite(f"Last {len(out)} invoice(s):\n\n" + "\n\n".join(out),
                 f"Zoho Invoice, {numbers}")


@skill
def billing_profile(customer: str):
    """Everything needed to raise an invoice, in one call: resolves the customer by
    name, gives their standing rate and clerkage percentage, and lists the exact case
    numbers and cause titles already billed. Reuse those exact titles rather than
    retyping a case number from what someone said. Call this before
    zoho_create_appearance_invoice."""
    z = _zoho()
    wanted = customer.strip()
    if wanted.isdigit():
        contact_id = contact_name = wanted
    else:
        hits = z.customers(wanted)
        if not hits:
            return f"No Zoho customer matching '{customer}'."
        if len(hits) > 1:
            listing = "\n".join(f"  {c['contact_id']} | {c['contact_name']}" for c in hits[:10])
            return f"Several customers match '{customer}' — which one?\n{listing}"
        contact_id, contact_name = hits[0]["contact_id"], hits[0]["contact_name"]

    prof = billing.profile(z, contact_id)
    lines = [f"Customer: {contact_name} (id {contact_id})"]
    if prof["rate"] is None:
        lines.append("No invoice history for this customer — ask for the fee and the "
                     "exact cause title before drafting.")
        return _cite("\n".join(lines), "Zoho Invoice, contacts")

    lines.append(f"Standing rate: {prof['rate']:g} (Appearance & Arguments), "
                 f"clerkage {prof['clerkage_pct']}%")
    lines.append("Matters previously billed (use these exact cause titles):")
    lines += [f"  {m['case']} | {m['title']} | last billed {m['last_billed']}"
              for m in prof["matters"]]
    lines += [f"  (could not parse, read as-is) {u}" for u in prof["unparsed"]]
    return _cite("\n".join(lines),
                 f"Zoho Invoice, {', '.join(prof['invoice_numbers'][:5])}")


# The firm's house format, measured from the live account on 2026-08-03. Every
# appearance invoice is these exact two lines, so build them here rather than
# asking the model to assemble line items freehand: the template can't drift,
# and the clerkage arithmetic isn't left to a language model.
APPEARANCE_HEADING = "Appearance & Arguments"
CLERKAGE_HEADING = "Clerkage"  # historical invoices misspell this "Clearkage"
DEFAULT_COURT = "Meghalaya High Court at Shillong"


@skill
def zoho_create_appearance_invoice(customer_id: str, hearing_date: str, case_number: str,
                                   cause_title: str, fee: float = 0,
                                   court: str = DEFAULT_COURT, clerkage_pct: float = 0):
    """Create a DRAFT appearance invoice in the firm's house format: an
    'Appearance & Arguments' line plus a clerkage line at clerkage_pct of the fee.
    hearing_date is YYYY-MM-DD. cause_title is the full cause title, e.g.
    'M/S Kamakshi Ispat Ltd. Vs. Meghalaya Power Distribution Corporation Ltd. & Ors.'.
    Leave fee and clerkage_pct out and the customer's standing rate is used; pass a fee
    only when told a different amount. Refuses to bill the same hearing twice.
    NOT sent to anyone."""
    from datetime import datetime
    try:
        day = datetime.strptime(hearing_date.strip(), "%Y-%m-%d")
    except (ValueError, AttributeError):
        raise ValueError(f"hearing_date must be YYYY-MM-DD, got {hearing_date!r}")

    stamp = day.strftime("%d.%m.%Y")
    z = _zoho()

    already = billing.find_duplicate(z, customer_id, case_number, stamp)
    if already:
        return (f"Not created: {already} already bills {case_number} for {stamp}. "
                f"Check that invoice in Zoho before raising another.")

    # The rate comes from history rather than from the model, which cannot then
    # invent one. Only an explicit instruction overrides it.
    if not fee or fee <= 0 or not clerkage_pct:
        prof = billing.profile(z, customer_id)
        if not fee or fee <= 0:
            fee = prof["rate"]
            if not fee:
                raise ValueError("No invoice history for this customer, so I do not know "
                                 "the fee to bill — tell me the amount.")
        if not clerkage_pct:
            clerkage_pct = prof["clerkage_pct"] or 10

    description = (f"Appearance and arguments on {stamp} in "
                   f"{case_number} titled as {cause_title} before {court}.")
    clerkage = round(fee * clerkage_pct / 100.0, 2)
    items = [
        {"name": APPEARANCE_HEADING, "description": description, "rate": fee, "quantity": 1},
        {"name": CLERKAGE_HEADING, "description": f"@ {clerkage_pct:g}%",
         "rate": clerkage, "quantity": 1},
    ]
    inv = z.create_draft(customer_id, items)
    total = inv.get("total")
    total_txt = f"₹{total:.2f}" if isinstance(total, (int, float)) else "amount unavailable"
    return _cite(f"Draft created: {inv.get('invoice_number', '?')} for {total_txt} "
                 f"({APPEARANCE_HEADING} ₹{fee:g} + {CLERKAGE_HEADING} ₹{clerkage:g}, "
                 f"invoice_id={inv.get('invoice_id', '?')}). NOT sent — review it in Zoho.",
                 f"Zoho Invoice, {inv.get('invoice_number', '?')}")


@skill
def zoho_create_draft_invoice(customer_id: str, item_description: str, amount: float, quantity: float = 1):
    """Create a DRAFT invoice in Zoho. It is NOT sent to anyone. Report invoice number, total and invoice_id back; the firm reviews and finalizes drafts in the Zoho dashboard."""
    inv = _zoho().create_draft(customer_id, [{"name": item_description, "rate": amount, "quantity": quantity}])
    total = inv.get("total")
    total_txt = f"₹{total:.2f}" if isinstance(total, (int, float)) else "amount unavailable"
    return (f"Draft created: {inv.get('invoice_number', '?')} for {total_txt} "
            f"(invoice_id={inv.get('invoice_id', '?')}, status={inv.get('status', 'draft')}). "
            f"NOT sent — review it in the Zoho dashboard.")
