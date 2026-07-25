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
