import json

from fastapi.testclient import TestClient

import server

GROUP = "120363000000000000@g.us"


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "HIST_DIR", tmp_path)
    monkeypatch.setattr(server, "_group", lambda: GROUP)
    monkeypatch.setattr(server, "_ask",
                        lambda text, hist: (f"echo:{text}", (hist or []) + [{"role": "user", "content": text}]))
    return TestClient(server.app)


def test_chat_in_group_replies_and_persists(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    r = c.post("/chat", json={"chat": GROUP, "sender": "Adv. Mehta", "text": "hi"})
    assert r.status_code == 200 and r.json()["reply"] == "echo:Adv. Mehta: hi"
    assert json.loads((tmp_path / "group.json").read_text())


def test_chat_other_chat_403(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    assert c.post("/chat", json={"chat": "911@s.whatsapp.net", "sender": "x", "text": "hi"}).status_code == 403


def test_history_truncated(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    for i in range(80):
        c.post("/chat", json={"chat": GROUP, "sender": "s", "text": f"m{i}"})
    hist = json.loads((tmp_path / "group.json").read_text())
    assert len(hist) <= server.MAX_HISTORY


def test_corrupted_history_recovers(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    hf = tmp_path / "group.json"
    # Write corrupted JSON to history file
    hf.write_text("{corrupt")
    # POST should recover gracefully
    r = c.post("/chat", json={"chat": GROUP, "sender": "Adv. Mehta", "text": "hi"})
    assert r.status_code == 200
    assert r.json()["reply"] == "echo:Adv. Mehta: hi"
    # Verify history file now contains valid JSON
    hist = json.loads(hf.read_text())
    assert isinstance(hist, list)


def test_trim_history_drops_leading_orphan_after_truncation():
    # 4 old plain messages, then an orphaned tool item (no "role"), then two
    # more plain messages -- truncating to the last 3 items should land
    # exactly on the orphan first, and it must be dropped rather than
    # persisted at the front of the saved history.
    hist = [{"role": "user", "content": f"old{i}"} for i in range(4)]
    hist.append({"type": "function_call_output", "call_id": "c1", "output": "orphan"})
    hist += [{"role": "assistant", "content": "ok"}, {"role": "user", "content": "new"}]
    trimmed = server._trim_history(hist, max_items=3)
    assert trimmed == [{"role": "assistant", "content": "ok"}, {"role": "user", "content": "new"}]


def test_trim_history_all_orphans_yields_empty():
    hist = [{"type": "function_call", "call_id": "c1"}, {"type": "function_call_output", "call_id": "c1"}]
    assert server._trim_history(hist, max_items=10) == []


def test_self_heals_from_poisoned_persisted_history(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    hf = tmp_path / "group.json"
    # Simulate a previously-persisted history that's poisoned (a stranded
    # function_call_output with no role) -- this is what would 400 the
    # Responses API on every subsequent request without the retry.
    hf.write_text(json.dumps([{"type": "function_call_output", "call_id": "c1", "output": "x"}]))

    def flaky_ask(text, hist):
        if hist:
            raise RuntimeError("simulated 400: stranded tool item in history")
        return f"echo:{text}", [{"role": "user", "content": text}]

    monkeypatch.setattr(server, "_ask", flaky_ask)
    r = c.post("/chat", json={"chat": GROUP, "sender": "Adv. Mehta", "text": "hi"})
    assert r.status_code == 200
    assert r.json()["reply"] == "echo:Adv. Mehta: hi"
    hist = json.loads(hf.read_text())
    assert hist == [{"role": "user", "content": "Adv. Mehta: hi"}]


def test_ask_fails_both_attempts_returns_apology_without_saving(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    hf = tmp_path / "group.json"
    hf.write_text(json.dumps([{"type": "function_call_output", "call_id": "c1", "output": "x"}]))

    def always_fails(text, hist):
        raise RuntimeError("still broken")

    monkeypatch.setattr(server, "_ask", always_fails)
    r = c.post("/chat", json={"chat": GROUP, "sender": "Adv. Mehta", "text": "hi"})
    assert r.status_code == 200
    assert r.json()["reply"] == server._APOLOGY
    # Poisoned history left untouched since neither attempt succeeded
    hist = json.loads(hf.read_text())
    assert hist == [{"type": "function_call_output", "call_id": "c1", "output": "x"}]
