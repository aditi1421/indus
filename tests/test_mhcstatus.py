"""The AJAX payloads here mirror what meghalayahighcourt.nic.in actually returned
on 2026-08-03 for WP(C) 348 of 2026, trimmed to the parts we parse.
"""

import pytest

import mhcstatus

RESULT_TABLE = (
    '<div><table class="dataTable"><thead><tr>'
    '<th>Case No.</th><th>Petitioner</th><th>Respondent</th><th>Status</th>'
    '</tr></thead><tbody><tr>'
    '<td>WP(C)/348/2026</td><td>DHAR CONSTRUCTION COMPANY</td>'
    '<td>MEGHALAYA POWER DISTRIBUTION CORPORATION LTD &amp; ORS</td><td>Pending</td>'
    '</tr></tbody></table></div>')

SUCCESS = [
    {"command": "settings", "settings": {}},
    {"command": "insert", "data": '<div class="math-captcha">7 + 5 equals</div>'},
    {"command": "insert", "data": RESULT_TABLE},
]

NO_MATCH = [
    {"command": "insert", "data": '<div class="math-captcha">3 + 1 equals</div>'},
    {"command": "insert", "data": "<div>No record found</div>"},
]


def test_solves_an_addition_question():
    assert mhcstatus.solve_question("99 + 2") == 101


def test_solves_a_subtraction_question():
    assert mhcstatus.solve_question(" 12 - 5 ") == 7


def test_solves_a_multiplication_question():
    assert mhcstatus.solve_question("4 x 3") == 12


def test_refuses_a_question_it_cannot_read():
    with pytest.raises(ValueError, match="captcha"):
        mhcstatus.solve_question("what is the airspeed velocity")


def test_parses_the_result_row_out_of_the_ajax_commands():
    rows = mhcstatus.parse_results(SUCCESS)

    assert len(rows) == 1
    assert rows[0]["case_no"] == "WP(C)/348/2026"
    assert rows[0]["petitioner"] == "DHAR CONSTRUCTION COMPANY"
    assert rows[0]["status"] == "Pending"
    assert "MEGHALAYA POWER DISTRIBUTION" in rows[0]["respondent"]


def test_a_response_with_no_table_parses_to_nothing():
    assert mhcstatus.parse_results(NO_MATCH) == []


def test_the_refreshed_captcha_block_is_not_mistaken_for_a_result():
    """Every response carries a fresh captcha question; it must not become a row."""
    rows = mhcstatus.parse_results([{"command": "insert", "data": "<div>7 + 5 equals</div>"}])

    assert rows == []


def test_a_successful_lookup_reports_what_it_found():
    res = mhcstatus.mhc_case_status("WP(C)", 348, 2026, fetch=lambda *a: SUCCESS)

    assert res["found"] is True
    assert res["error"] is None
    assert res["results"][0]["case_no"] == "WP(C)/348/2026"


def test_a_genuine_no_match_is_not_reported_as_an_error():
    res = mhcstatus.mhc_case_status("WP(C)", 99999, 2026, fetch=lambda *a: NO_MATCH)

    assert res["found"] is False
    assert res["error"] is None


def test_a_portal_failure_is_reported_as_an_error_not_as_nothing_found():
    """The whole point: 'the site broke' must never read as 'no such case'."""
    def boom(*a):
        raise RuntimeError("connection reset")

    res = mhcstatus.mhc_case_status("WP(C)", 348, 2026, fetch=boom)

    assert res["error"]
    assert res["found"] is False


def test_a_successful_lookup_is_cached():
    calls = []

    def fetch(*a):
        calls.append(a)
        return SUCCESS

    mhcstatus.clear_cache()
    mhcstatus.mhc_case_status("WP(C)", 348, 2026, fetch=fetch)
    mhcstatus.mhc_case_status("WP(C)", 348, 2026, fetch=fetch)

    assert len(calls) == 1


def test_a_failure_is_never_cached():
    """Caching an outage would keep serving it after the portal recovered."""
    outcomes = [RuntimeError("down"), SUCCESS]

    def fetch(*a):
        got = outcomes.pop(0)
        if isinstance(got, Exception):
            raise got
        return got

    mhcstatus.clear_cache()
    mhcstatus.mhc_case_status("WP(C)", 348, 2026, fetch=fetch)
    second = mhcstatus.mhc_case_status("WP(C)", 348, 2026, fetch=fetch)

    assert second["found"] is True
