"""The firm's court matters, and the difference between "not listed" and "we couldn't look".

Found in production 2026-08-04: the bot reported "no firm matters listed" every
day. It was matching government file numbers from the register (File No.
LJ(B)57/2024) against cause lists, which carry court case numbers. That search
can never hit, so the answer was structurally guaranteed and wrong.

Separately, listings_for computed which courts were unavailable and discarded
it, so a source outage read as "nothing listed" — the failure most likely to
cost someone a hearing.
"""

import pandas as pd
import pytest

import cases


@pytest.fixture
def matters(monkeypatch):
    tab = pd.DataFrame([
        {"COURT": "sc", "CASE NO": "", "PARTIES": "All AMT matters", "AOR CODE": "2648"},
        {"COURT": "mhc", "CASE NO": "WP(C)/348/2026", "PARTIES": "Dhar vs MePDCL",
         "AOR CODE": ""},
    ])
    monkeypatch.setattr(cases, "_matters_tab", lambda: tab)
    return tab


def test_a_case_number_row_is_searched_by_case_number(matters):
    rows = cases.court_matters()

    mhc = [r for r in rows if r["court"] == "mhc"][0]
    assert mhc["token"] == "WP(C)/348/2026"


def test_a_row_with_no_case_number_is_searched_by_aor_code(matters):
    """One Supreme Court row keyed on the AOR code covers every matter that
    advocate is on, including ones filed after the sheet was last edited."""
    rows = cases.court_matters()

    sc = [r for r in rows if r["court"] == "sc"][0]
    assert sc["token"] == "2648"


def test_rows_with_neither_identifier_are_dropped(monkeypatch):
    monkeypatch.setattr(cases, "_matters_tab", lambda: pd.DataFrame(
        [{"COURT": "mhc", "CASE NO": "", "PARTIES": "no idea", "AOR CODE": ""}]))

    assert cases.court_matters() == []


def test_a_missing_tab_is_reported_not_silently_empty(monkeypatch):
    def missing():
        raise ValueError("no such tab")

    monkeypatch.setattr(cases, "_matters_tab", missing)

    with pytest.raises(ValueError, match="COURT MATTERS"):
        cases.court_matters()


# --- the dangerous bug: unavailable must never read as nothing listed ---


def _stub_lookup(monkeypatch, available, hits):
    def fetch(court, date):
        if court not in available:
            raise ValueError(f"no list for {court}")
        return [b"pdf"]

    def search(court, date, query):
        return hits.get((court, query), [])

    monkeypatch.setattr(cases, "_fetch", fetch)
    monkeypatch.setattr(cases, "_search", search)


def test_a_match_is_reported_with_the_court_that_was_checked(matters, monkeypatch):
    _stub_lookup(monkeypatch, {"sc", "mhc"},
                 {("mhc", "WP(C)/348/2026"): ["ITEM 5 ... WP(C)/348/2026"]})

    result = cases.listings_for("2026-08-05")

    assert len(result["rows"]) == 1
    assert result["unavailable"] == []
    assert set(result["checked"]) == {"sc", "mhc"}


def test_no_match_with_every_list_published_is_a_real_nil(matters, monkeypatch):
    _stub_lookup(monkeypatch, {"sc", "mhc"}, {})

    result = cases.listings_for("2026-08-05")

    assert result["rows"] == []
    assert result["unavailable"] == []


def test_an_unavailable_court_is_named_rather_than_treated_as_nothing_listed(matters, monkeypatch):
    _stub_lookup(monkeypatch, {"mhc"}, {})

    result = cases.listings_for("2026-08-05")

    assert result["unavailable"] == ["sc"]
    assert result["checked"] == ["mhc"]


def test_every_list_unavailable_means_we_know_nothing(matters, monkeypatch):
    _stub_lookup(monkeypatch, set(), {})

    result = cases.listings_for("2026-08-05")

    assert set(result["unavailable"]) == {"sc", "mhc"}
    assert result["checked"] == []


def test_the_skill_refuses_to_claim_nothing_is_listed_when_it_could_not_look(matters, monkeypatch):
    import skills
    _stub_lookup(monkeypatch, set(), {})

    out = skills.todays_causelist_matches("2026-08-05")

    assert "could not" in out.lower() or "unavailable" in out.lower()
    assert "no firm matters found in published" not in out.lower()


def test_the_skill_says_plainly_when_lists_were_read_and_nothing_matched(matters, monkeypatch):
    import skills
    _stub_lookup(monkeypatch, {"sc", "mhc"}, {})

    out = skills.todays_causelist_matches("2026-08-05")

    assert "no firm matters" in out.lower()
