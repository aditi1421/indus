import time

import requests

AUTH = "https://accounts.zoho.in/oauth/v2/token"
BASE = "https://www.zohoapis.in/invoice/v3"


class Zoho:
    def __init__(self, client_id, client_secret, refresh, org):
        self.client_id, self.client_secret = client_id, client_secret
        self.refresh, self.org = refresh, org
        self._tok, self._exp = None, 0.0

    @classmethod
    def from_cfg(cls):
        from config import get_cfg
        c = get_cfg()
        return cls(c.zoho_client_id, c.zoho_client_secret, c.zoho_refresh, c.zoho_org)

    def _token(self):
        if self._tok and time.time() < self._exp - 60:
            return self._tok
        r = requests.post(AUTH, data={
            "refresh_token": self.refresh, "client_id": self.client_id,
            "client_secret": self.client_secret, "grant_type": "refresh_token"})
        r.raise_for_status()
        d = r.json()
        if "access_token" not in d:
            raise ValueError(f"Zoho auth failed: {d}")
        self._tok, self._exp = d["access_token"], time.time() + d.get("expires_in", 3600)
        return self._tok

    def _req(self, method, path, **kw):
        headers = {"Authorization": f"Zoho-oauthtoken {self._token()}",
                   "X-com-zoho-invoice-organizationid": self.org}
        r = requests.request(method, f"{BASE}{path}", headers=headers, timeout=30, **kw)
        r.raise_for_status()
        return r.json()

    def customers(self, search=""):
        params = {"contact_name_contains": search} if search else {}
        return self._req("GET", "/contacts", params=params).get("contacts", [])

    def invoices(self, customer_id="", limit=10):
        """Recent invoices, newest first, optionally for one customer."""
        params = {"per_page": limit, "sort_column": "date", "sort_order": "D"}
        if customer_id:
            params["customer_id"] = customer_id
        return self._req("GET", "/invoices", params=params).get("invoices", [])

    def invoice(self, invoice_id):
        """One invoice including its line items; the list endpoint omits them."""
        return self._req("GET", f"/invoices/{invoice_id}")["invoice"]

    def create_draft(self, customer_id, items):
        line_items = []
        for i in items:
            li = {"name": i["name"], "rate": i["rate"], "quantity": i.get("quantity", 1)}
            # The firm's house format carries the real detail (hearing date, case
            # number, cause title) in description; name is just the heading.
            if i.get("description"):
                li["description"] = i["description"]
            line_items.append(li)
        payload = {"customer_id": customer_id, "line_items": line_items}
        return self._req("POST", "/invoices", json=payload)["invoice"]

    def email_invoice(self, invoice_id):
        return self._req("POST", f"/invoices/{invoice_id}/email", json={})
