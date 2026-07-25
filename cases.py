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


def firm_cases() -> pd.DataFrame:
    df = _raw_sheet()
    rename = {}
    for col in df.columns:
        for sub, canon in COLMAP.items():
            if sub in col.lower() and canon not in rename.values():
                rename[col] = canon
                break
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
