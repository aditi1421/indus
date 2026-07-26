import re
from datetime import datetime

import pandas as pd

import aides
from causelists import search as _causelist_search

# lowercase substring of real header -> canonical name. Order matters for pass 2
# of _map_columns: "depart" is tried (and can claim DEPARTMENT for `client`)
# before "part" gets a chance to claim it for `parties` (since "department"
# itself contains the substring "part").
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

    Pass 1: case-insensitive exact match (stripped) against a COLMAP key wins
    outright, so "Court" binds to `court` before "Court Fee" gets a chance.
    Pass 2: for any canonical name still unbound, fall back to substring
    matching among the remaining unclaimed columns; if more than one
    unclaimed column matches, that's ambiguous and we refuse to guess.
    """
    bound = {}  # source column -> canonical name
    claimed = set()  # canonical names already bound

    for col in columns:
        key = col.strip().lower()
        if key in COLMAP:
            canon = COLMAP[key]
            if canon not in claimed:
                bound[col] = canon
                claimed.add(canon)

    for sub, canon in COLMAP.items():
        if canon in claimed:
            continue
        candidates = [c for c in columns if c not in bound and sub in c.strip().lower()]
        if len(candidates) > 1:
            raise ValueError(
                f"Ambiguous column mapping for '{canon}': candidates {candidates} "
                f"all match substring '{sub}'. Rename the sheet columns to disambiguate."
            )
        if candidates:
            bound[candidates[0]] = canon
            claimed.add(canon)

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
