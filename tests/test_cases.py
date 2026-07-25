import pytest
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


def _fake_sheet_with_court_fee():
    return pd.DataFrame({
        "Case No": ["W.P.(C) 678/2026", "SLP(C) 12345/2025"],
        "Parties": ["ACME INFRA LTD Vs UOI", "RAM KUMAR Vs STATE"],
        "Court Fee": ["500", "1000"],
        "Court": ["DHC", "SC"],
        "Client": ["Acme Infra", "Ram Kumar"],
    })


def test_firm_cases_exact_match_beats_substring(monkeypatch):
    monkeypatch.setattr(cases, "_raw_sheet", _fake_sheet_with_court_fee)
    df = cases.firm_cases()
    assert list(df.columns) == ["case_no", "parties", "court", "client"]
    # "Court" must bind to `court`, not "Court Fee" -- values stay sc/dhc, not "500"/"1000"
    assert df.court.tolist() == ["dhc", "sc"]


def _fake_sheet_ambiguous_case_columns():
    return pd.DataFrame({
        "Case No": ["W.P.(C) 678/2026"],
        "Case Ref": ["ABC-123"],
        "Parties": ["ACME INFRA LTD Vs UOI"],
        "Court": ["DHC"],
        "Client": ["Acme Infra"],
    })


def test_firm_cases_ambiguous_substring_raises(monkeypatch):
    monkeypatch.setattr(cases, "_raw_sheet", _fake_sheet_ambiguous_case_columns)
    with pytest.raises(ValueError) as exc:
        cases.firm_cases()
    msg = str(exc.value)
    assert "case_no" in msg
    assert "Case No" in msg
    assert "Case Ref" in msg


def test_listings_for_rejects_malformed_date(monkeypatch):
    monkeypatch.setattr(cases, "_raw_sheet", _fake_sheet)

    def fake_search(court, date, query):
        raise AssertionError("_search should not be called for a malformed date")

    monkeypatch.setattr(cases, "_search", fake_search)
    with pytest.raises(ValueError) as exc:
        cases.listings_for("24-07-2026")
    assert "YYYY-MM-DD" in str(exc.value)
