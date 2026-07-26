import digest


def test_run_posts_one_group_message(monkeypatch):
    rows = [
        {"case_no": "A/1", "client": "C1", "court": "sc", "parties": "p", "matches": ["1. A/1 ..."]},
        {"case_no": "B/2", "client": "C2", "court": "dhc", "parties": "p", "matches": ["2. B/2 ..."]},
    ]
    monkeypatch.setattr(digest, "_listings", lambda date: rows)
    sent = []
    monkeypatch.setattr(digest, "_post", lambda url, text: sent.append(text))
    assert digest.run(date="2026-07-24") == 1
    assert len(sent) == 1
    assert "A/1" in sent[0] and "B/2" in sent[0]
    assert "Supreme Court" in sent[0] and "Delhi HC" in sent[0]


def test_run_no_listings_sends_nothing(monkeypatch):
    monkeypatch.setattr(digest, "_listings", lambda date: [])
    monkeypatch.setattr(digest, "_post", lambda *a: (_ for _ in ()).throw(AssertionError("should not post")))
    assert digest.run(date="2026-07-24") == 0
