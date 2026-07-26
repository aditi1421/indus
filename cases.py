import re
from datetime import datetime

import pandas as pd

import aides
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


def _raw_sheet() -> pd.DataFrame:
    from config import get_cfg
    return aides.read_gsheet(get_cfg().sheet_indus)


def _search(court, date, query):
    return _causelist_search(court, date, query)


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


def listings_for(date: str) -> list[dict]:
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"date must be YYYY-MM-DD, got {date!r}")
    out = []
    for _, row in firm_cases().iterrows():
        if row.court not in ("sc", "dhc", "mhc"):
            continue
        try:
            matches = _search(row.court, date, search_token(row.case_no))
        except ValueError:
            continue  # list not published for this court/date
        if matches:
            out.append({**row.to_dict(), "matches": matches})
    return out
