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
    monkeypatch.setattr(cases, "_fetch", lambda court, date: None)

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


# --- Task 5b: real firm sheet is a file register, not a court-case sheet ---


def _fake_register_sheet():
    return pd.DataFrame({
        "FILE": ["File No. LR(B)6/2026", "Letter No. MS/VI-768/2020/31"],
        "DEPARTMENT": ["Law", "Education"],
        "RECEIPT DATE": ["01/01/2026", "02/02/2026"],
        "REMARKS": [None, "pending verification"],
        "ASSIGNED": ["A. Bora", "C. Das"],
        "STATUS": ["Open", "Closed"],
    })


def test_firm_cases_maps_register_schema(monkeypatch):
    monkeypatch.setattr(cases, "_raw_sheet", _fake_register_sheet)
    df = cases.firm_cases()
    assert list(df.columns) == ["case_no", "parties", "court", "client"]
    assert df.case_no.tolist() == ["File No. LR(B)6/2026", "Letter No. MS/VI-768/2020/31"]
    assert df.client.tolist() == ["Law", "Education"]
    # REMARKS was NaN for row 0 -> must become "", not the string "nan"
    assert df.parties.tolist() == ["", "pending verification"]
    # no court column in the real sheet -> every row defaults to mhc
    assert df.court.tolist() == ["mhc", "mhc"]


# --- review finding: a column's first-matching alias must consume it, even
# when its canon is already claimed, so it can't fall through to a later
# alias that belongs to a different canon (e.g. Department -> "depart" ->
# `client`, claimed by a real Client column, must not then bind to `parties`
# via the later "part" alias). ---


def test_map_columns_department_alias_consumed_by_earlier_claim():
    assert cases._map_columns(["Client", "Department", "Case No"]) == {
        "Client": "client",
        "Case No": "case_no",
    }


def test_map_columns_department_alias_consumed_with_real_parties_present():
    # Department's first match ("depart" -> client) is still consumed even
    # though a genuine Parties column is present -- no ambiguity error, and
    # Department stays unbound rather than stealing the parties binding.
    assert cases._map_columns(["Client", "Department", "Parties", "Case No"]) == {
        "Client": "client",
        "Parties": "parties",
        "Case No": "case_no",
    }


def test_search_token_strips_file_no_prefix():
    assert cases.search_token("File No. LR(B)6/2026") == "LR(B)6/2026"


def test_search_token_strips_letter_no_prefix():
    assert cases.search_token("Letter No. MS/VI-768/2020/31") == "MS/VI-768/2020/31"


def test_search_token_uses_first_segment_before_ampersand():
    assert cases.search_token("File No. A & File No. B") == "A"


def test_search_token_leaves_plain_case_no_unchanged():
    assert cases.search_token("W.P.(C) 678/2026") == "W.P.(C) 678/2026"


def test_listings_for_uses_search_token(monkeypatch):
    monkeypatch.setattr(cases, "_raw_sheet", _fake_register_sheet)
    monkeypatch.setattr(cases, "_fetch", lambda court, date: None)
    captured = []

    def fake_search(court, date, query):
        captured.append(query)
        return []

    monkeypatch.setattr(cases, "_search", fake_search)
    cases.listings_for("2026-07-24")
    assert captured == ["LR(B)6/2026", "MS/VI-768/2020/31"]


# --- review finding: per-row paid browser fallback for an unpublished court
# must not run once per row -- fetch() is resolved once per DISTINCT court,
# and rows whose court is unavailable are skipped without ever calling
# _search (the expensive per-row boundary). ---


def _fake_sheet_five_mhc_rows():
    return pd.DataFrame({
        "Case No": [f"WP(C) {i}/2026" for i in range(5)],
        "Parties": ["A Vs B"] * 5,
        "Court": ["MHC"] * 5,
        "Client": [f"Client{i}" for i in range(5)],
    })


def test_listings_for_resolves_court_once_and_skips_unavailable_rows(monkeypatch):
    monkeypatch.setattr(cases, "_raw_sheet", _fake_sheet_five_mhc_rows)

    fetch_calls = []

    def fake_fetch(court, date):
        fetch_calls.append(court)
        raise ValueError("not published")

    def fake_search(court, date, query):
        raise AssertionError("_search must not be called for an unavailable court")

    monkeypatch.setattr(cases, "_fetch", fake_fetch)
    monkeypatch.setattr(cases, "_search", fake_search)

    out = cases.listings_for("2026-07-24")
    assert out == []
    assert fetch_calls == ["mhc"]  # exactly one call despite 5 rows


def test_firm_register_skill_shows_status_and_no_nan(monkeypatch):
    import skills

    def fake_raw_register():
        return pd.DataFrame({
            "FILE": ["File No. LR(B)6/2026"],
            "DEPARTMENT": ["Law"],
            "RECEIPT DATE": ["01/01/2026"],
            "REMARKS": [""],
            "ASSIGNED": ["A. Bora"],
            "STATUS": ["Open"],
        })

    monkeypatch.setattr(cases, "raw_register", fake_raw_register)
    out = skills.firm_register()
    assert "Open" in out
    assert "nan" not in out.lower()
