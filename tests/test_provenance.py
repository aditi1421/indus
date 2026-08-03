import json

import pytest

import provenance
import skills


@pytest.fixture
def log(tmp_path, monkeypatch):
    path = tmp_path / "provenance.jsonl"
    monkeypatch.setattr(provenance, "LOG", path)
    provenance.clear()
    yield path
    provenance.clear()


def test_cite_appends_one_source_tag(log):
    out = skills._cite("2 matters listed", "Delhi HC cause list, 2026-08-04")

    assert out.endswith("[source: Delhi HC cause list, 2026-08-04]")
    assert out.count("[source:") == 1


def test_cite_records_the_source_against_the_live_request(log):
    provenance.start(chat="group@g.us", sender="Aditi", question="what is listed")

    skills._cite("2 matters listed", "Delhi HC cause list, 2026-08-04")

    assert provenance.sources() == ["Delhi HC cause list, 2026-08-04"]


def test_recording_outside_a_request_does_not_blow_up(log):
    skills._cite("something", "Zoho Invoice")  # no start() called

    assert provenance.sources() == []


def test_finish_writes_one_json_line_carrying_the_sources(log):
    provenance.start(chat="group@g.us", sender="Aditi", question="what is listed")
    skills._cite("2 matters", "Delhi HC cause list, 2026-08-04")

    provenance.finish("Two matters are listed.")

    lines = log.read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["chat"] == "group@g.us"
    assert entry["sender"] == "Aditi"
    assert entry["sources"] == ["Delhi HC cause list, 2026-08-04"]
    assert entry["ts"]


def test_finish_without_a_start_writes_nothing(log):
    provenance.finish("orphan answer")

    assert not log.exists()


def test_recent_returns_only_the_asked_for_chat(log):
    for chat in ("group@g.us", "other@g.us", "group@g.us"):
        provenance.start(chat=chat, sender="X", question="q")
        skills._cite("t", f"source for {chat}")
        provenance.finish("a")

    entries = provenance.recent("group@g.us", limit=10)

    assert len(entries) == 2
    assert all(e["chat"] == "group@g.us" for e in entries)


def test_recent_tolerates_a_corrupt_line(log):
    provenance.start(chat="group@g.us", sender="X", question="q")
    skills._cite("t", "good source")
    provenance.finish("a")
    with log.open("a") as fh:
        fh.write("{not json\n")

    assert len(provenance.recent("group@g.us", limit=10)) == 1


# --- skills actually carry their source ---


def test_zoho_find_customer_says_the_answer_came_from_zoho(log, monkeypatch):
    class Stub:
        def customers(self, name):
            return [{"contact_id": "1", "contact_name": "Meghalaya Energy Corporation Limited"}]

    monkeypatch.setattr(skills, "_zoho", lambda: Stub())

    assert "[source:" in skills.zoho_find_customer("Meghalaya")


def test_a_miss_is_not_dressed_up_with_a_source(log, monkeypatch):
    """Nothing was retrieved, so there is nothing to cite."""
    class Stub:
        def customers(self, name):
            return []

    monkeypatch.setattr(skills, "_zoho", lambda: Stub())

    assert "[source:" not in skills.zoho_find_customer("nobody")


def test_recent_sources_skill_reports_what_was_cited(log):
    provenance.start(chat="group@g.us", sender="Aditi", question="what is listed")
    skills._cite("2 matters", "Delhi HC cause list, 2026-08-04")
    provenance.finish("Two matters are listed.")
    provenance.start(chat="group@g.us", sender="Aditi", question="where from?")

    out = skills.recent_sources()

    assert "Delhi HC cause list, 2026-08-04" in out
