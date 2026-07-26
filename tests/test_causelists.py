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


def test_fetch_falls_back_to_browser_use(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "CACHE", tmp_path)
    monkeypatch.setattr(cl, "pdf_links", lambda c, d: [])
    monkeypatch.setattr(cl, "_fetch_via_browser", lambda court, date: "1. W.P.(C) 1/2026 A Vs B")
    p = cl.fetch("sc", "2026-07-24")
    assert "W.P.(C) 1/2026" in p.read_text()


def test_fetch_raises_when_fallback_also_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "CACHE", tmp_path)
    monkeypatch.setattr(cl, "pdf_links", lambda c, d: [])
    monkeypatch.setattr(cl, "_fetch_via_browser", lambda court, date: None)
    with pytest.raises(ValueError):
        cl.fetch("sc", "2026-07-24")

    assert not (tmp_path / "sc_2026-07-24.txt").exists()


def test_fetch_mhc_falls_back_to_browser_use(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "CACHE", tmp_path)
    monkeypatch.setattr(cl, "_mhc_pdfs", lambda date: [])
    monkeypatch.setattr(cl, "_fetch_via_browser", lambda court, date: "1. WP(C) 9/2026 X Vs Y")
    p = cl.fetch("mhc", "2026-07-24")
    assert "WP(C) 9/2026" in p.read_text()


def test_fetch_mhc_raises_when_fallback_also_whitespace(tmp_path, monkeypatch):
    date = "2026-07-24"
    monkeypatch.setattr(cl, "CACHE", tmp_path)
    monkeypatch.setattr(cl, "_mhc_pdfs", lambda d: [])
    monkeypatch.setattr(cl, "_fetch_via_browser", lambda court, date: "   \n\t  ")
    with pytest.raises(ValueError):
        cl.fetch("mhc", date)
    assert not (tmp_path / f"mhc_{date}.txt").exists()


def test_fetch_via_browser_handles_sdk_exception(monkeypatch):
    """Test that _fetch_via_browser returns None when browser SDK raises."""
    import sys
    import types

    # Create fake SDK module that raises on .run()
    fake_browser_sdk = types.ModuleType("browser_use_sdk")

    class FakeBrowserUseFails:
        def __init__(self, api_key):
            self.api_key = api_key

        def run(self, task):
            raise RuntimeError("Simulated browser SDK failure")

    fake_browser_sdk.BrowserUse = FakeBrowserUseFails

    # Create fake config module
    fake_config = types.ModuleType("config")

    class FakeCfg:
        key_browser_use = "test-key"

    fake_config.get_cfg = lambda: FakeCfg()

    # Inject into sys.modules before calling the function
    monkeypatch.setitem(sys.modules, "browser_use_sdk", fake_browser_sdk)
    monkeypatch.setitem(sys.modules, "config", fake_config)

    result = cl._fetch_via_browser("sc", "2026-07-24")
    assert result is None


def test_fetch_via_browser_rejects_empty_output(monkeypatch):
    """Test that _fetch_via_browser returns None when output is empty or None."""
    import sys
    import types

    fake_browser_sdk = types.ModuleType("browser_use_sdk")

    class FakeBrowserUseEmptyOutput:
        def __init__(self, api_key):
            self.api_key = api_key

        def run(self, task):
            # Return object with empty output
            class Result:
                output = ""

            return Result()

    fake_browser_sdk.BrowserUse = FakeBrowserUseEmptyOutput

    fake_config = types.ModuleType("config")

    class FakeCfg:
        key_browser_use = "test-key"

    fake_config.get_cfg = lambda: FakeCfg()

    monkeypatch.setitem(sys.modules, "browser_use_sdk", fake_browser_sdk)
    monkeypatch.setitem(sys.modules, "config", fake_config)

    result = cl._fetch_via_browser("sc", "2026-07-24")
    assert result is None


def test_fetch_negative_cache_avoids_repeat_scrape_and_fallback(tmp_path, monkeypatch):
    # review finding: an unpublished court/date must not re-trigger the full
    # scrape + paid browser-use fallback on every call -- a short-lived
    # negative cache should short-circuit the second call entirely.
    monkeypatch.setattr(cl, "CACHE", tmp_path)
    monkeypatch.setattr(cl, "_NEGATIVE", {})

    calls = {"links": 0, "browser": 0}

    def fake_links(court, date):
        calls["links"] += 1
        return []

    def fake_browser(court, date):
        calls["browser"] += 1
        return None

    monkeypatch.setattr(cl, "pdf_links", fake_links)
    monkeypatch.setattr(cl, "_fetch_via_browser", fake_browser)

    with pytest.raises(ValueError):
        cl.fetch("sc", "2026-07-24")
    with pytest.raises(ValueError, match="cached result"):
        cl.fetch("sc", "2026-07-24")

    assert calls["links"] == 1
    assert calls["browser"] == 1


def test_fetch_negative_cache_cleared_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "CACHE", tmp_path)
    # TTL=0 means the stale-looking pre-existing entry never short-circuits
    # the call, so we can observe that a successful fetch clears it.
    monkeypatch.setattr(cl, "NEGATIVE_TTL", 0)
    monkeypatch.setattr(cl, "_NEGATIVE", {("sc", "2026-07-24"): __import__("time").time()})
    monkeypatch.setattr(cl, "pdf_links", lambda c, d: ["https://x/y.pdf"])
    monkeypatch.setattr(cl, "pdf_to_text", lambda path: "1. Some Case No. 1/2026 A Vs B\n")

    class _FakeResp:
        content = b"pdf-bytes"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(cl.requests, "get", lambda *a, **kw: _FakeResp())

    cl.fetch("sc", "2026-07-24")
    assert ("sc", "2026-07-24") not in cl._NEGATIVE


def test_fetch_negative_cache_expires_after_ttl(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "CACHE", tmp_path)
    monkeypatch.setattr(cl, "NEGATIVE_TTL", 1800)
    stale = __import__("time").time() - 1801
    monkeypatch.setattr(cl, "_NEGATIVE", {("sc", "2026-07-24"): stale})

    calls = {"links": 0}

    def fake_links(court, date):
        calls["links"] += 1
        return []

    monkeypatch.setattr(cl, "pdf_links", fake_links)
    monkeypatch.setattr(cl, "_fetch_via_browser", lambda court, date: None)

    with pytest.raises(ValueError):
        cl.fetch("sc", "2026-07-24")
    assert calls["links"] == 1  # stale entry did not short-circuit


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
