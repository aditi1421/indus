from dataclasses import dataclass

from wraps import SSM

APP = "/apps/courts"

KEYS = {
    "bucket": "/apps/bucket",
    "key_openai": "/core/openai/key_openai",
    "key_gemini": "/core/google/key_gemini",
    "sheet_indus": f"{APP}/sheet_indus",
    "key_indus": f"{APP}/key_indus",
    "key_browser_use": f"{APP}/key_browser_use",
    "zoho_client_id": f"{APP}/zoho_client_id",
    "zoho_client_secret": f"{APP}/zoho_client_secret",
    "zoho_refresh": f"{APP}/zoho_refresh",
    "zoho_org": f"{APP}/zoho_org",
    "group_jid": f"{APP}/whatsapp_group",
    "sc_search_terms": f"{APP}/sc_search_terms",
}


@dataclass
class Config:
    bucket: str
    key_openai: str
    key_gemini: str
    sheet_indus: str
    key_indus: str
    key_browser_use: str
    zoho_client_id: str
    zoho_client_secret: str
    zoho_refresh: str
    zoho_org: str
    group_jid: str
    sc_search_terms: str

    @classmethod
    def load(cls):
        ssm = SSM().get(list(KEYS.values()))
        missing = [path for path in KEYS.values() if ssm.get(path) is None]
        if missing:
            raise ValueError(
                f"Missing/None SSM parameter(s), check they exist and the instance "
                f"role can read them: {', '.join(sorted(missing))}"
            )
        return cls(**{k: ssm[v] for k, v in KEYS.items()})


_cfg = None


def get_cfg() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = Config.load()
    return _cfg
