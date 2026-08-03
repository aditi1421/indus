import json

import pytest

import notes


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the notes module at a throwaway file."""
    path = tmp_path / "notes.json"
    monkeypatch.setattr(notes, "NOTES", path)
    return path


def test_load_returns_empty_when_file_missing(store):
    assert notes.load() == []


def test_add_persists_the_note(store):
    notes.add("MeECL means Meghalaya Energy Corporation Limited", added_by="Aditi")

    saved = notes.load()
    assert len(saved) == 1
    assert saved[0]["text"] == "MeECL means Meghalaya Energy Corporation Limited"
    assert saved[0]["added_by"] == "Aditi"
    assert saved[0]["id"]
    assert saved[0]["added_at"]


def test_ids_are_unique_across_adds(store):
    a = notes.add("first")
    b = notes.add("second")

    assert a["id"] != b["id"]
    assert {n["id"] for n in notes.load()} == {a["id"], b["id"]}


def test_remove_deletes_only_the_named_note(store):
    keep = notes.add("keep me")
    drop = notes.add("drop me")

    assert notes.remove(drop["id"]) is True

    remaining = notes.load()
    assert [n["id"] for n in remaining] == [keep["id"]]


def test_remove_unknown_id_reports_failure(store):
    notes.add("only note")

    assert notes.remove("no-such-id") is False
    assert len(notes.load()) == 1


def test_corrupt_file_degrades_to_empty(store):
    store.write_text("{not valid json")

    assert notes.load() == []


def test_add_survives_a_corrupt_file(store):
    store.write_text("{not valid json")

    notes.add("written over the corruption")

    assert [n["text"] for n in notes.load()] == ["written over the corruption"]


def test_block_contains_the_note_text(store):
    notes.add("MeECL means Meghalaya Energy Corporation Limited")

    block = notes.block()

    assert "MeECL means Meghalaya Energy Corporation Limited" in block


def test_block_is_empty_when_nothing_taught(store):
    assert notes.block() == ""


def test_block_truncates_to_the_char_cap_and_says_so(store):
    for i in range(50):
        notes.add(f"note number {i} " + "x" * 100)

    block = notes.block(max_chars=500)

    assert len(block) <= 700  # cap plus the trailing advisory line
    assert "list_notes" in block


def test_writes_are_atomic_leaving_no_temp_file(store):
    notes.add("a note")

    leftovers = list(store.parent.glob("*.tmp"))
    assert leftovers == []


def test_stored_file_is_valid_json(store):
    notes.add("a note")

    assert isinstance(json.loads(store.read_text()), list)


# --- the skills the lawyers actually call ---


def test_remember_note_confirms_what_it_saved(store):
    import skills

    out = skills.remember_note("MeECL means Meghalaya Energy Corporation Limited", taught_by="Aditi")

    assert "MeECL means Meghalaya Energy Corporation Limited" in out
    assert [n["text"] for n in notes.load()] == [
        "MeECL means Meghalaya Energy Corporation Limited"]


def test_list_notes_shows_the_id_and_who_taught_it(store):
    import skills

    skills.remember_note("PWD means Public Works Department", taught_by="Aditi")

    out = skills.list_notes()
    assert "PWD means Public Works Department" in out
    assert "Aditi" in out


def test_list_notes_says_so_when_nothing_taught(store):
    import skills

    assert "nothing" in skills.list_notes().lower()


def test_forget_note_removes_it(store):
    import skills

    note = notes.add("temporary")

    out = skills.forget_note(note["id"])

    assert notes.load() == []
    assert "forgot" in out.lower() or "removed" in out.lower()


def test_forget_note_reports_an_unknown_id(store):
    import skills

    out = skills.forget_note("999")

    assert "999" in out
    assert "no note" in out.lower()
