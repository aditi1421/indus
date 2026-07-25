from datetime import datetime

import pandas as pd

import aides
from causelists import search as _causelist_search

COLMAP = {  # lowercase substring of real header -> canonical name (adjust from Step 1)
    "case": "case_no",
    "part": "parties",
    "court": "court",
    "client": "client",
}
COURT_ALIASES = {"sc": "sc", "supreme": "sc", "dhc": "dhc", "delhi": "dhc",
                 "mhc": "mhc", "meghalaya": "mhc"}


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


def firm_cases() -> pd.DataFrame:
    df = _raw_sheet()
    rename = _map_columns(list(df.columns))
    df = df.rename(columns=rename)
    need = ("case_no", "parties", "court", "client")
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError(f"Firm case sheet is missing columns {missing}; has {list(df.columns)}")
    df = df[list(need)].dropna(subset=["case_no"])
    df["court"] = (df["court"].astype(str).str.strip().str.lower()
                   .map(lambda s: next((v for k, v in COURT_ALIASES.items() if k in s), s)))
    return df.reset_index(drop=True)


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
            matches = _search(row.court, date, str(row.case_no))
        except ValueError:
            continue  # list not published for this court/date
        if matches:
            out.append({**row.to_dict(), "matches": matches})
    return out
