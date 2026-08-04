"""The firm's Supreme Court caseload, queried by AOR code.

Better than matching the advocate's name in cause-list text: this is the
registry's own answer rather than an inference, so it also tells the bot which
matters exist before any of them are listed.

Payloads mirror what sci.gov.in returned for AOR 2648 on 2026-08-04, including
its zero-padded, stray-dashed case numbers.
"""

import casestatus
import pytest

RAW = [
    {"serial": "1", "diary_no": "62866", "diary_year": "2025",
     "case_number": "SLP(C) No. 001614 -  / 2026", "registered_on": "10-01-2026",
     "petitioner": "LIONS VIDYA MANDIR", "respondent": "LIONS CLUB BAREILLY",
     "status": "PENDING"},
    {"serial": "2", "diary_no": "5494", "diary_year": "2017",
     "case_number": "Crl.A. No. 001971 / 2026", "registered_on": "16-04-2026",
     "petitioner": "SHANKAR MAHTO", "respondent": "THE STATE OF BIHAR",
     "status": "PENDING"},
]


def test_padded_case_numbers_are_tidied_for_searching():
    """A cause list prints "SLP(C) No. 1614/2026", not "001614 -  / 2026"."""
    assert casestatus.tidy_case_no("SLP(C) No. 001614 -  / 2026") == "SLP(C) No. 1614/2026"


def test_tidying_leaves_an_already_clean_number_alone():
    assert casestatus.tidy_case_no("Crl.A. No. 1971/2026") == "Crl.A. No. 1971/2026"


def test_aor_cases_returns_the_registry_rows(monkeypatch):
    monkeypatch.setattr(casestatus, "_run_search",
                        lambda *a, **kw: {"found": True, "results": RAW, "error": None})
    casestatus.clear_cache()

    res = casestatus.sc_aor_cases("2648", 2026)

    assert res["found"] is True
    assert len(res["results"]) == 2
    assert res["results"][0]["case_number"] == "SLP(C) No. 1614/2026"


def test_the_query_uses_the_registry_field_codes(monkeypatch):
    """Sending "Pending" instead of "P" is rejected as an invalid field."""
    captured = {}

    def fake(action, page, fields, **kw):
        captured.update({"action": action, "fields": fields})
        return {"found": True, "results": [], "error": None}

    monkeypatch.setattr(casestatus, "_run_search", fake)
    casestatus.clear_cache()

    casestatus.sc_aor_cases("2648", 2026)

    assert captured["action"] == "get_case_status_aor_code"
    assert captured["fields"]["aor_code"] == "2648"
    assert captured["fields"]["case_status"] == "P"
    assert captured["fields"]["party_type"] == "any"


def test_a_failed_query_is_not_cached(monkeypatch):
    outcomes = [{"found": False, "results": [], "error": "portal down"},
                {"found": True, "results": RAW, "error": None}]
    monkeypatch.setattr(casestatus, "_run_search", lambda *a, **kw: outcomes.pop(0))
    casestatus.clear_cache()

    casestatus.sc_aor_cases("2648", 2026)

    assert casestatus.sc_aor_cases("2648", 2026)["found"] is True


def test_a_successful_query_is_cached(monkeypatch):
    calls = []

    def fake(*a, **kw):
        calls.append(1)
        return {"found": True, "results": RAW, "error": None}

    monkeypatch.setattr(casestatus, "_run_search", fake)
    casestatus.clear_cache()

    casestatus.sc_aor_cases("2648", 2026)
    casestatus.sc_aor_cases("2648", 2026)

    assert len(calls) == 1
