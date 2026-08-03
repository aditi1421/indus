import re
from datetime import datetime

import pandas as pd

import aides
from causelists import fetch as _causelist_fetch
from causelists import search as _causelist_search

# lowercase substring of real header -> canonical name. Order matters for the
# substring-matching pass of _map_columns: for the schemas this file currently
# supports, "depart" must be tried (and claim DEPARTMENT for `client`) before
# "part" gets a chance to claim it for `parties` (since "department" itself
# contains the substring "part"). A column's first match in this order is the
# only alias it is ever considered for -- see _map_columns.
COLMAP = {
    "case": "case_no",
    "file": "case_no",
    "client": "client",
    "depart": "client",
    "part": "parties",
    "remark": "parties",
    "court": "court",
}
COURT_ALIASES = {"sc": "sc", "supreme": "sc", "dhc": "dhc", "delhi": "dhc",
                 "mhc": "mhc", "meghalaya": "mhc"}

# Register FILE values look like "File No. LR(B)6/2026" or
# "Letter No. MS/VI-768/2020/31" -- strip that label before using the value
# as a cause-list search token (the full value is still what's displayed).
_PREFIX_RE = re.compile(r"^\s*(?:file|letter)\s*no\.?:?\s*", re.IGNORECASE)


MATTERS_TAB = "COURT MATTERS"


def _raw_sheet() -> pd.DataFrame:
    from config import get_cfg
    return aides.read_gsheet(get_cfg().sheet_indus)


def _matters_tab() -> pd.DataFrame:
    """The firm's court matters tab, read by name rather than by gid so the
    firm can move it around without anything breaking."""
    from urllib.parse import quote
    from config import get_cfg
    url = (f"https://docs.google.com/spreadsheets/d/{get_cfg().sheet_indus}"
           f"/gviz/tq?tqx=out:csv&sheet={quote(MATTERS_TAB)}")
    return pd.read_csv(url)


def _first_col(columns, *names):
    lookup = {str(c).strip().lower(): c for c in columns}
    for name in names:
        if name in lookup:
            return lookup[name]
    return None


def _cell(row, col):
    if not col:
        return ""
    value = str(row.get(col, "") or "").strip()
    return "" if value.lower() == "nan" else value


def court_matters() -> list[dict]:
    """The firm's court matters, each with the token to search a cause list for.

    The government file register cannot serve this purpose: it holds FILE and
    letter numbers ("File No. LJ(B)57/2024"), while cause lists carry court
    case numbers. Matching one against the other can never hit, which is why
    the bot reported nothing listed every day until 2026-08-04.

    A row is searched by its case number, or by its AOR code when it has none.
    One Supreme Court row keyed on an AOR code therefore covers every matter
    that advocate is on, including ones filed since the sheet was last edited.
    """
    try:
        df = _matters_tab()
    except Exception as e:
        raise ValueError(
            f"Could not read the '{MATTERS_TAB}' tab of the firm sheet: {e}")

    court_col = _first_col(df.columns, "court")
    if not court_col:
        raise ValueError(f"The '{MATTERS_TAB}' tab needs a COURT column; "
                         f"found {list(df.columns)}")
    case_col = _first_col(df.columns, "case no", "case_no", "case number", "case")
    parties_col = _first_col(df.columns, "parties", "party")
    aor_col = _first_col(df.columns, "aor code", "aor", "aor_code")

    out = []
    for _, row in df.iterrows():
        court = _cell(row, court_col).lower()
        court = next((v for k, v in COURT_ALIASES.items() if k in court), court)
        if court not in ("sc", "dhc", "mhc"):
            continue
        case_no, aor = _cell(row, case_col), _cell(row, aor_col)
        token = case_no or aor
        if not token:
            continue  # nothing to search for; a row like this is just a note
        out.append({"court": court, "token": token, "case_no": case_no,
                    "aor": aor, "parties": _cell(row, parties_col)})
    return out


def _search(court, date, query):
    return _causelist_search(court, date, query)


def _fetch(court, date):
    return _causelist_fetch(court, date)


def _map_columns(columns: list[str]) -> dict:
    """Bind real sheet headers to canonical names.

    Each column is resolved to its FIRST matching alias in COLMAP order, up
    front, and that resolution is final: a column can never be reconsidered
    for a different canonical name via some later alias, even if the canon
    its first match points to turns out to be claimed by something else.
    This is what prevents e.g. a "Department" column (whose first match is
    "depart" -> `client`) from falling through to the later "part" -> `parties`
    alias just because `client` was already bound to a real "Client" column.

    A full case-insensitive match (stripped) against a COLMAP key ("exact")
    outranks a mere substring match ("sub") for the same canon: "Court" binds
    to `court` outright, and "Court Fee" -- whose first (and only) match is
    also `court`, via substring -- is simply excluded rather than fought over.

    For a given canon, if the exact-match candidate pool is non-empty it wins
    outright (ambiguous only if more than one column exactly matches, e.g. two
    differently-cased headers); otherwise the substring-match pool applies:
    more than one candidate is ambiguous and we refuse to guess.
    """
    # column -> (kind, alias, canon) of its first COLMAP match, "exact" or "sub"
    first_match = {}
    for col in columns:
        key = col.strip().lower()
        if key in COLMAP:
            first_match[col] = ("exact", key, COLMAP[key])
            continue
        for sub, canon in COLMAP.items():
            if sub in key:
                first_match[col] = ("sub", sub, canon)
                break

    by_canon = {}  # canon -> {"exact": [(col, alias), ...], "sub": [(col, alias), ...]}
    for col, (kind, alias, canon) in first_match.items():
        by_canon.setdefault(canon, {"exact": [], "sub": []})[kind].append((col, alias))

    bound = {}
    for canon, kinds in by_canon.items():
        pool = kinds["exact"] or kinds["sub"]
        if len(pool) > 1:
            cols = [c for c, _ in pool]
            aliases = sorted({a for _, a in pool})
            raise ValueError(
                f"Ambiguous column mapping for '{canon}': candidates {cols} "
                f"match alias(es) {aliases}. Rename the sheet columns to disambiguate."
            )
        if pool:
            bound[pool[0][0]] = canon

    return bound


def _clean(series: pd.Series) -> pd.Series:
    """Blank out NaN cells instead of letting them stringify to the literal 'nan'."""
    return series.where(series.notna(), "").astype(str)


def search_token(case_no: str) -> str:
    """Derive a cause-list search token from a case_no/FILE value.

    Strips a leading "File No."/"Letter No." label (case-insensitive, optional
    trailing dot/colon) and, if the value chains multiple files with " & ",
    keeps only the first segment. The original case_no is unaffected -- it's
    still what gets displayed to the user.
    """
    s = str(case_no).strip()
    s = _PREFIX_RE.sub("", s)
    if " & " in s:
        s = s.split(" & ", 1)[0]
    return s.strip()


def raw_register() -> pd.DataFrame:
    """The entire firm sheet, un-normalized, every column as-is with NaN -> ''."""
    df = _raw_sheet()
    return df.where(df.notna(), "")


def firm_cases() -> pd.DataFrame:
    df = _raw_sheet()
    rename = _map_columns(list(df.columns))
    df = df.rename(columns=rename)
    if "case_no" not in df.columns:
        raise ValueError(
            f"Firm case sheet is missing required column 'case_no' "
            f"(bind a header containing 'case' or 'file'); has {list(df.columns)}"
        )
    df = df.dropna(subset=["case_no"]).copy()
    for col in ("parties", "client"):
        if col not in df.columns:
            df[col] = ""
    if "court" not in df.columns:
        df["court"] = "mhc"
    else:
        df["court"] = _clean(df["court"]).str.strip().str.lower()
        df["court"] = df["court"].map(lambda s: next((v for k, v in COURT_ALIASES.items() if k in s), s))
    df["case_no"] = _clean(df["case_no"])
    df["parties"] = _clean(df["parties"])
    df["client"] = _clean(df["client"])
    return df[["case_no", "parties", "court", "client"]].reset_index(drop=True)


def listings_for(date: str) -> dict:
    """{"rows": [...], "checked": [courts], "unavailable": [courts]}.

    The availability of each court's list is part of the answer, not an
    internal detail. Without it, a source outage is indistinguishable from
    "nothing is listed" — the failure most likely to cost someone a hearing.
    """
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"date must be YYYY-MM-DD, got {date!r}")

    matters = court_matters()

    # Resolve availability once per court, not once per matter: the fallback
    # path is slow and, for the browser fallback, paid.
    checked, unavailable = [], []
    for court in sorted({m["court"] for m in matters}):
        try:
            _fetch(court, date)
            checked.append(court)
        except ValueError:
            unavailable.append(court)

    rows = []
    for matter in matters:
        if matter["court"] in unavailable:
            continue
        try:
            matches = _search(matter["court"], date, matter["token"])
        except ValueError:
            continue
        if matches:
            rows.append({**matter, "matches": matches})
    return {"rows": rows, "checked": checked, "unavailable": unavailable}
