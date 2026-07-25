import pandas as pd
import cases


def _fake_sheet():
    return pd.DataFrame({
        "Case No": ["W.P.(C) 678/2026", "SLP(C) 12345/2025"],
        "Parties": ["ACME INFRA LTD Vs UOI", "RAM KUMAR Vs STATE"],
        "Court": ["DHC", "SC"],
        "Client": ["Acme Infra", "Ram Kumar"],
    })


def test_firm_cases_normalizes(monkeypatch):
    monkeypatch.setattr(cases, "_raw_sheet", _fake_sheet)
    df = cases.firm_cases()
    assert list(df.columns) == ["case_no", "parties", "court", "client"]
    assert df.court.tolist() == ["dhc", "sc"]


def test_listings_for_matches_only_listed(monkeypatch):
    monkeypatch.setattr(cases, "_raw_sheet", _fake_sheet)

    def fake_search(court, date, query):
        if court == "dhc" and "678/2026" in query:
            return ["2. W.P.(C) No. 678/2026 ACME INFRA LTD Vs UNION OF INDIA"]
        return []

    monkeypatch.setattr(cases, "_search", fake_search)
    out = cases.listings_for("2026-07-24")
    assert len(out) == 1
    assert out[0]["client"] == "Acme Infra"
    assert out[0]["matches"][0].startswith("2.")
