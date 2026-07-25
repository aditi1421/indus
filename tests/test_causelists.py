import pytest
import requests

import causelists as cl

# tests/fixtures/*.pdf are live-downloaded samples kept only as discovery provenance
# (see task-3-report.md); no test in this file reads them.

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


class _FakeHTMLResp:
    def __init__(self, text):
        self.text = text


def test_pdf_links_dedupes_sc_mirror_hosts_and_filters_by_date(monkeypatch):
    date = "2026-07-27"
    # Mirrors the real SC pattern: api.sci.gov.in / webapi.sci.gov.in serve identical
    # paths for the same file; a link for a different date must be filtered out.
    html = """
    <a href="https://api.sci.gov.in/jonew/cl/2026-07-27/M_J_1.pdf">M_J_1</a>
    <a href="https://webapi.sci.gov.in/jonew/cl/2026-07-27/M_J_1.pdf">mirror</a>
    <a href="https://api.sci.gov.in/jonew/cl/2026-07-28/M_J_1.pdf">wrong date</a>
    """
    monkeypatch.setattr(cl.requests, "get", lambda *a, **kw: _FakeHTMLResp(html))
    links = cl.pdf_links(cl.COURTS["sc"], date)
    assert links == ["https://api.sci.gov.in/jonew/cl/2026-07-27/M_J_1.pdf"]


class _FakePDFResp:
    status_code = 200
    content = b"arbitrary-bytes-standing-in-for-a-pdf"

    def raise_for_status(self):
        pass


def test_dhc_fetch_retries_once_after_timeout_then_succeeds(tmp_path, monkeypatch):
    date = "2026-07-27"
    url = "https://delhihighcourt.nic.in/files/2026-07/cause-list/cause_list_for_27.07.2026.pdf"
    monkeypatch.setattr(cl, "CACHE", tmp_path)
    monkeypatch.setattr(cl, "pdf_links", lambda court, d: [url])
    monkeypatch.setattr(cl, "pdf_to_text", lambda path: "1. Some Case No. 1/2026 A Vs B\n")

    calls = {"n": 0}

    def fake_get(u, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.Timeout("simulated network drop")
        return _FakePDFResp()

    monkeypatch.setattr(cl.requests, "get", fake_get)
    out = cl.fetch("dhc", date)
    assert calls["n"] == 2
    assert out.is_file()
    assert out.read_text("utf-8").strip()


def test_dhc_fetch_raises_after_two_consecutive_failures(tmp_path, monkeypatch):
    date = "2026-07-27"
    url = "https://delhihighcourt.nic.in/files/2026-07/cause-list/cause_list_for_27.07.2026.pdf"
    monkeypatch.setattr(cl, "CACHE", tmp_path)
    monkeypatch.setattr(cl, "pdf_links", lambda court, d: [url])

    def fake_get(u, headers=None, timeout=None):
        raise requests.Timeout("simulated network drop")

    monkeypatch.setattr(cl.requests, "get", fake_get)
    with pytest.raises(requests.Timeout):
        cl.fetch("dhc", date)


def test_fetch_refuses_to_cache_whitespace_only_extracted_text(tmp_path, monkeypatch):
    date = "2026-07-27"
    url = "https://delhihighcourt.nic.in/files/2026-07/cause-list/cause_list_for_27.07.2026.pdf"
    monkeypatch.setattr(cl, "CACHE", tmp_path)
    monkeypatch.setattr(cl, "pdf_links", lambda court, d: [url])
    monkeypatch.setattr(cl, "pdf_to_text", lambda path: "   \n\t\n  ")  # scanned-image PDF
    monkeypatch.setattr(cl.requests, "get", lambda *a, **kw: _FakePDFResp())

    with pytest.raises(ValueError, match="no extractable text"):
        cl.fetch("dhc", date)

    assert not (tmp_path / f"dhc_{date}.txt").exists()


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
