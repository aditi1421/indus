import pytest

import config


@pytest.fixture(autouse=True)
def _reset_cfg_singleton():
    config._cfg = None
    yield
    config._cfg = None


def test_load_maps_ssm_values(monkeypatch):
    fake = {
        "/apps/bucket": "b",
        "/core/openai/key_openai": "ko",
        "/core/google/key_gemini": "kg",
        "/apps/courts/sheet_indus": "si",
        "/apps/courts/key_indus": "ki",
        "/apps/courts/key_browser_use": "kb",
        "/apps/courts/zoho_client_id": "zi",
        "/apps/courts/zoho_client_secret": "zs",
        "/apps/courts/zoho_refresh": "zr",
        "/apps/courts/zoho_org": "zo",
        "/apps/courts/whatsapp_group": "120363000000000000@g.us",
        "/apps/courts/sc_search_terms": "AVIJIT MANI",
    }

    class FakeSSM:
        def get(self, keys):
            return {k: fake[k] for k in keys}

    monkeypatch.setattr(config, "SSM", FakeSSM)
    cfg = config.Config.load()
    assert cfg.bucket == "b"
    assert cfg.zoho_org == "zo"
    assert cfg.group_jid == "120363000000000000@g.us"


def test_load_raises_clear_error_when_ssm_value_missing(monkeypatch):
    # wraps.SSM can come back with None for a key on a ClientError; that must
    # not silently propagate as Config(some_field=None) -- it should fail
    # loudly, naming the missing parameter path(s).
    fake = {
        "/apps/bucket": "b",
        "/core/openai/key_openai": "ko",
        "/core/google/key_gemini": "kg",
        "/apps/courts/sheet_indus": "si",
        "/apps/courts/key_indus": "ki",
        "/apps/courts/key_browser_use": None,  # missing
        "/apps/courts/zoho_client_id": "zi",
        "/apps/courts/zoho_client_secret": "zs",
        "/apps/courts/zoho_refresh": "zr",
        "/apps/courts/zoho_org": "zo",
        "/apps/courts/whatsapp_group": "120363000000000000@g.us",
        "/apps/courts/sc_search_terms": "AVIJIT MANI",
    }

    class FakeSSM:
        def get(self, keys):
            return {k: fake[k] for k in keys}

    monkeypatch.setattr(config, "SSM", FakeSSM)
    with pytest.raises(ValueError) as exc:
        config.Config.load()
    assert "/apps/courts/key_browser_use" in str(exc.value)


def test_get_cfg_caches_single_load(monkeypatch):
    calls = []
    sentinel = object()

    def fake_load():
        calls.append(1)
        return sentinel

    monkeypatch.setattr(config.Config, "load", staticmethod(fake_load))
    assert config.get_cfg() is sentinel
    assert config.get_cfg() is sentinel
    assert len(calls) == 1
