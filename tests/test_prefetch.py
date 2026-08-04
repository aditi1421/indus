"""Interactive questions must read a cache, never build one.

Measured 2026-08-04: the Supreme Court publishes its cause list as many
multi-megabyte PDFs. Fetching and extracting them exceeded 280 seconds on this
box (2 cores, 1.8 GB), while the gateway gives the agent 180 seconds. So a
lawyer asking "what is listed today" got no answer at all.
"""

import causelists
import prefetch
import pytest


def test_a_cached_list_is_returned_without_touching_the_network(tmp_path, monkeypatch):
    monkeypatch.setattr(causelists, "CACHE", tmp_path)
    (tmp_path / "sc_2026-08-04.txt").write_text("ITEM 1 ...", "utf-8")

    def explode(*a, **kw):
        raise AssertionError("must not fetch when the cache is warm")

    monkeypatch.setattr(causelists, "_fetch_impl", explode)

    assert causelists.fetch("sc", "2026-08-04", network=False).name == "sc_2026-08-04.txt"


def test_a_cold_cache_says_not_fetched_yet_rather_than_downloading(tmp_path, monkeypatch):
    monkeypatch.setattr(causelists, "CACHE", tmp_path)

    def explode(*a, **kw):
        raise AssertionError("network=False must never fetch")

    monkeypatch.setattr(causelists, "_fetch_impl", explode)

    with pytest.raises(ValueError, match="not been fetched"):
        causelists.fetch("sc", "2026-08-04", network=False)


def test_search_honours_the_cache_only_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(causelists, "CACHE", tmp_path)

    with pytest.raises(ValueError, match="not been fetched"):
        causelists.search("sc", "2026-08-04", "anything", network=False)


def test_listings_never_reach_the_network(monkeypatch):
    """The whole point: answering a question must not download a cause list."""
    import cases
    seen = {}

    def fake_fetch(court, date, *, network=True):
        seen["network"] = network
        raise ValueError("not been fetched yet")

    monkeypatch.setattr(cases, "_causelist_fetch", fake_fetch)
    monkeypatch.setattr(cases, "_sc_terms", lambda: ["AVIJIT MANI"])
    monkeypatch.setattr(cases, "_sc_aor_matters", lambda: [])
    monkeypatch.setattr(cases, "_matters_tab", lambda: (_ for _ in ()).throw(ValueError("no tab")))

    result = cases.listings_for("2026-08-04")

    assert seen["network"] is False
    assert result["unavailable"] == ["sc"]


def test_prefetch_warms_every_court_it_is_asked_for(monkeypatch):
    calls = []
    monkeypatch.setattr(prefetch, "_fetch",
                        lambda court, date: calls.append((court, date)))

    prefetch.run(dates=["2026-08-04"], courts=("sc", "mhc"))

    assert calls == [("sc", "2026-08-04"), ("mhc", "2026-08-04")]


def test_one_failing_court_does_not_stop_the_others(monkeypatch):
    done = []

    def flaky(court, date):
        if court == "sc":
            raise ValueError("not published")
        done.append(court)

    monkeypatch.setattr(prefetch, "_fetch", flaky)

    prefetch.run(dates=["2026-08-04"], courts=("sc", "mhc"))

    assert done == ["mhc"]
