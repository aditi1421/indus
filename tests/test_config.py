import config


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
    }

    class FakeSSM:
        def get(self, keys):
            return {k: fake[k] for k in keys}

    monkeypatch.setattr(config, "SSM", FakeSSM)
    cfg = config.Config.load()
    assert cfg.bucket == "b"
    assert cfg.zoho_org == "zo"
    assert cfg.group_jid == "120363000000000000@g.us"
