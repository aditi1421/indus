"""The reply shape here mirrors api.exa.ai's documented /search response,
trimmed to the fields we parse.
"""

import pytest

import research
import skills

EXA_REPLY = {
    "results": [
        {
            "title": "Anticipatory bail in cheque bounce cases: SC clarifies",
            "url": "https://www.livelaw.in/top-stories/sc-anticipatory-bail",
            "publishedDate": "2026-07-14T00:00:00.000Z",
            "highlights": ["The Supreme Court held that anticipatory bail may be granted."],
        },
        {
            "title": "Dhar Construction Co vs State of Meghalaya",
            "url": "https://indiankanoon.org/doc/1234567/",
            "publishedDate": None,
            "highlights": [],
        },
    ]
}


def test_parse_results_extracts_title_url_date_and_highlight():
    rows = research.parse_results(EXA_REPLY)
    assert rows[0] == {
        "title": "Anticipatory bail in cheque bounce cases: SC clarifies",
        "url": "https://www.livelaw.in/top-stories/sc-anticipatory-bail",
        "published": "2026-07-14",
        "highlight": "The Supreme Court held that anticipatory bail may be granted.",
    }


def test_parse_results_tolerates_missing_date_and_highlights():
    rows = research.parse_results(EXA_REPLY)
    assert rows[1]["published"] == ""
    assert rows[1]["highlight"] == ""
    assert rows[1]["url"] == "https://indiankanoon.org/doc/1234567/"


def test_parse_results_of_empty_reply_is_empty():
    assert research.parse_results({}) == []
    assert research.parse_results(None) == []


def test_payload_restricts_search_to_legal_domains():
    payload = research._payload("anticipatory bail", 5)
    assert payload["includeDomains"] == research.LEGAL_DOMAINS
    assert payload["numResults"] == 5
    assert payload["query"] == "anticipatory bail"


def test_search_caches_repeat_queries_for_a_day():
    calls = []

    def fetch(query, num_results):
        calls.append(query)
        return EXA_REPLY

    first = research.search("Anticipatory Bail", fetch=fetch)
    second = research.search("  anticipatory bail ", fetch=fetch)
    assert first["found"] and second["found"]
    assert len(calls) == 1  # same query modulo case/whitespace


def test_search_reports_portal_failure_as_error_not_empty():
    def fetch(query, num_results):
        raise RuntimeError("connection reset")

    res = research.search("anticipatory bail", fetch=fetch)
    assert res["found"] is False
    assert res["results"] == []
    assert "connection reset" in res["error"]


def test_search_failure_is_not_cached():
    replies = [RuntimeError("down"), EXA_REPLY]

    def fetch(query, num_results):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    assert research.search("q", fetch=fetch)["found"] is False
    assert research.search("q", fetch=fetch)["found"] is True


def test_fetch_without_configured_key_names_the_ssm_parameter(monkeypatch):
    class Cfg:
        key_exa = ""

    monkeypatch.setattr(research, "get_cfg", lambda: Cfg())
    with pytest.raises(ValueError) as exc:
        research._fetch("anticipatory bail", 5)
    assert "/apps/courts/key_exa" in str(exc.value)


# --- the legal_research skill ---


def test_skill_lists_each_result_with_title_and_url(monkeypatch):
    monkeypatch.setattr(research, "search", lambda q: {
        "found": True, "results": research.parse_results(EXA_REPLY), "error": None})
    out = skills.legal_research("anticipatory bail cheque bounce")
    assert "Anticipatory bail in cheque bounce cases: SC clarifies" in out
    assert "https://indiankanoon.org/doc/1234567/" in out
    assert "2026-07-14" in out
    assert "[source:" in out


def test_skill_reports_search_failure_without_a_source_tag(monkeypatch):
    monkeypatch.setattr(research, "search", lambda q: {
        "found": False, "results": [], "error": "HTTPError: 401"})
    out = skills.legal_research("anything")
    assert "failed" in out.lower()
    assert "HTTPError: 401" in out
    assert "[source:" not in out


def test_skill_reports_no_results_plainly(monkeypatch):
    monkeypatch.setattr(research, "search", lambda q: {
        "found": False, "results": [], "error": None})
    out = skills.legal_research("nonexistent query")
    assert "no result" in out.lower()
    assert "[source:" not in out
