import causelists as cl

SAMPLE = """SUPREME COURT OF INDIA
DAILY CAUSE LIST
COURT NO. 1
1. SLP(C) No. 12345/2025 RAM KUMAR Vs STATE OF NCT OF DELHI
   Mr. A. Advocate
2. W.P.(C) No. 678/2026 ACME INFRA LTD Vs UNION OF INDIA
   IA No. 4321/2026 - EXEMPTION FROM FILING
3. Crl.A. No. 99/2024 SITA DEVI Vs CBI
"""


def test_split_items_finds_numbered_blocks():
    items = cl.split_items(SAMPLE)
    assert len(items) == 3
    assert "12345/2025" in items[0]
    assert "EXEMPTION" in items[1]  # continuation lines stay with their item


def test_normalize_case_no():
    assert cl.normalize_case_no("W.P.(C) No. 678/2026") == "WPCNO678/2026"
    assert cl.normalize_case_no("slp(c) 12345/2025") == "SLPC12345/2025"


def test_search_text_by_case_no_and_party(tmp_path, monkeypatch):
    f = tmp_path / "sc_2026-07-24.txt"
    f.write_text(SAMPLE)
    monkeypatch.setattr(cl, "fetch", lambda court, date: f)
    assert len(cl.search("sc", "2026-07-24", "678/2026")) == 1
    assert len(cl.search("sc", "2026-07-24", "acme infra")) == 1
    assert cl.search("sc", "2026-07-24", "nonexistent") == []


import pytest


@pytest.mark.network
def test_fetch_live_today():
    import aides
    date = str(aides.now(tz="Asia/Kolkata").date())
    for key in ("sc", "dhc", "mhc"):
        try:
            p = cl.fetch(key, date)
            assert p.stat().st_size > 500
        except ValueError as e:
            print(f"{key}: {e}")  # not published is acceptable; broken parse is not
