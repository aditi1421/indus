import json
import pytest
import zoho
import skills


class FakeResp:
    def __init__(self, data):
        self._d = data
        self.status_code = 200

    def json(self):
        return self._d

    def raise_for_status(self):
        pass


def _client(monkeypatch, responses):
    z = zoho.Zoho(client_id="i", client_secret="s", refresh="r", org="o")
    calls = []

    def fake_request(method, url, **kw):
        calls.append((method, url, kw))
        return FakeResp(responses.pop(0))

    monkeypatch.setattr(zoho.requests, "request", fake_request)
    monkeypatch.setattr(zoho.requests, "post",
                        lambda url, **kw: FakeResp({"access_token": "tok", "expires_in": 3600}))
    return z, calls


def test_create_draft_payload(monkeypatch):
    z, calls = _client(monkeypatch, [{"invoice": {"invoice_id": "9", "invoice_number": "INV-9",
                                                  "total": 5000.0, "status": "draft"}}])
    inv = z.create_draft("c1", [{"name": "Drafting WP", "rate": 5000, "quantity": 1}])
    assert inv["invoice_number"] == "INV-9"
    method, url, kw = calls[0]
    assert method == "POST" and url.endswith("/invoices")
    assert kw["json"]["customer_id"] == "c1"
    assert kw["headers"]["X-com-zoho-invoice-organizationid"] == "o"
    assert kw["headers"]["Authorization"] == "Zoho-oauthtoken tok"


def test_email_invoice_hits_email_endpoint(monkeypatch):
    z, calls = _client(monkeypatch, [{"code": 0, "message": "sent"}])
    z.email_invoice("9")
    assert calls[0][1].endswith("/invoices/9/email")


def test_zoho_auth_failure_no_access_token(monkeypatch):
    """Zoho auth response without access_token should raise ValueError about auth failure."""
    z = zoho.Zoho(client_id="i", client_secret="s", refresh="r", org="o")

    # Monkeypatch auth POST to return error (no access_token)
    monkeypatch.setattr(zoho.requests, "post",
                        lambda url, **kw: FakeResp({"error": "invalid_client"}))

    with pytest.raises(ValueError, match="Zoho auth failed"):
        z.customers("test")


def test_create_draft_passes_line_item_description(monkeypatch):
    """The house format carries the detail in `description`, not just `name`."""
    z, calls = _client(monkeypatch, [{"invoice": {"invoice_id": "9", "invoice_number": "INV-9",
                                                  "total": 11000.0, "status": "draft"}}])
    z.create_draft("c1", [{"name": "Appearance & Arguments",
                           "description": "Appearance and arguments on 03.08.2026 in WP(C) No. 469 of 2020.",
                           "rate": 11000, "quantity": 1}])

    line = calls[0][2]["json"]["line_items"][0]
    assert line["description"].startswith("Appearance and arguments on 03.08.2026")


def test_invoices_requests_newest_first_for_one_customer(monkeypatch):
    z, calls = _client(monkeypatch, [{"invoices": [{"invoice_id": "1"}]}])

    out = z.invoices(customer_id="c1", limit=5)

    method, url, kw = calls[0]
    assert method == "GET" and url.endswith("/invoices")
    assert kw["params"]["customer_id"] == "c1"
    assert kw["params"]["per_page"] == 5
    assert kw["params"]["sort_column"] == "date"
    assert kw["params"]["sort_order"] == "D"
    assert out == [{"invoice_id": "1"}]


def test_invoice_fetches_a_single_invoice_with_line_items(monkeypatch):
    z, calls = _client(monkeypatch, [{"invoice": {"invoice_id": "9",
                                                  "line_items": [{"name": "Appearance & Arguments"}]}}])

    inv = z.invoice("9")

    assert calls[0][1].endswith("/invoices/9")
    assert inv["line_items"][0]["name"] == "Appearance & Arguments"


class _StubHistory:
    """A customer with one invoice in the firm's house format."""

    def invoices(self, customer_id="", limit=5):
        return [{"invoice_id": "1", "invoice_number": "INV-005007",
                 "date": "2026-07-31", "total": 12100.0, "status": "draft"}]

    def invoice(self, invoice_id):
        return {"invoice_number": "INV-005007", "date": "2026-07-31",
                "total": 12100.0, "status": "draft",
                "line_items": [
                    {"name": "Appearance & Arguments",
                     "description": "Appearance and arguments on 31.07.2026 in WP/C/348/2026.",
                     "rate": 11000.0, "quantity": 1.0},
                    {"name": "Clearkage", "description": "@ 10%",
                     "rate": 1100.0, "quantity": 1.0}]}


def test_recent_invoices_exposes_the_house_wording_and_rate(monkeypatch):
    monkeypatch.setattr(skills, "_zoho", lambda: _StubHistory())

    out = skills.zoho_recent_invoices("c1")

    assert "INV-005007" in out
    assert "Appearance & Arguments" in out
    assert "11000" in out


def test_recent_invoices_says_so_when_there_is_no_history(monkeypatch):
    class Empty:
        def invoices(self, customer_id="", limit=5):
            return []

    monkeypatch.setattr(skills, "_zoho", lambda: Empty())

    assert "no invoice history" in skills.zoho_recent_invoices("c1").lower()


class _CaptureDraft:
    """No billing history, so the duplicate guard finds nothing to object to."""

    def __init__(self):
        self.customer_id = None
        self.items = None

    def invoices(self, customer_id="", limit=10):
        return []

    def invoice(self, invoice_id):
        return {}

    def create_draft(self, customer_id, items):
        self.customer_id, self.items = customer_id, items
        return {"invoice_id": "9", "invoice_number": "INV-9",
                "total": 12100.0, "status": "draft"}


def test_appearance_invoice_builds_the_two_line_house_template(monkeypatch):
    stub = _CaptureDraft()
    monkeypatch.setattr(skills, "_zoho", lambda: stub)

    out = skills.zoho_create_appearance_invoice(
        "c1", "2026-08-03", "WP(C) No. 469 of 2020",
        "M/S Kamakshi Ispat Ltd. Vs. Meghalaya Power Distribution Corporation Ltd. & Ors.",
        fee=11000)

    assert len(stub.items) == 2
    fee_line, clerkage_line = stub.items
    assert fee_line["name"] == "Appearance & Arguments"
    assert fee_line["rate"] == 11000
    assert "03.08.2026" in fee_line["description"]
    assert "WP(C) No. 469 of 2020" in fee_line["description"]
    assert "M/S Kamakshi Ispat Ltd." in fee_line["description"]
    assert clerkage_line["rate"] == 1100.0
    assert "INV-9" in out


def test_appearance_invoice_writes_clerkage_correctly_spelled(monkeypatch):
    stub = _CaptureDraft()
    monkeypatch.setattr(skills, "_zoho", lambda: stub)

    skills.zoho_create_appearance_invoice(
        "c1", "2026-08-03", "WP/C/348/2026", "Dhar Construction Company Vs. MePDCL", fee=11000)

    assert stub.items[1]["name"] == "Clerkage"
    assert "Clearkage" not in json.dumps(stub.items)


def test_appearance_invoice_computes_clerkage_rather_than_trusting_the_caller(monkeypatch):
    stub = _CaptureDraft()
    monkeypatch.setattr(skills, "_zoho", lambda: stub)

    skills.zoho_create_appearance_invoice(
        "c1", "2026-08-03", "CRP/22/2025", "MePDCL Vs. Dhar Construction Company",
        fee=7000, clerkage_pct=10)

    assert stub.items[1]["rate"] == 700.0


def test_appearance_invoice_rejects_an_unparseable_hearing_date(monkeypatch):
    monkeypatch.setattr(skills, "_zoho", lambda: _CaptureDraft())

    with pytest.raises(ValueError, match="hearing_date"):
        skills.zoho_create_appearance_invoice(
            "c1", "3rd August", "WP/C/348/2026", "A Vs. B", fee=11000)


def test_zoho_create_draft_invoice_missing_total(monkeypatch):
    """zoho_create_draft_invoice should handle missing total gracefully."""
    # Create a stub Zoho that returns invoice without total
    class StubZoho:
        def create_draft(self, customer_id, items):
            return {"invoice_id": "9", "invoice_number": "INV-9", "status": "draft"}

    monkeypatch.setattr(skills, "_zoho", lambda: StubZoho())

    result = skills.zoho_create_draft_invoice("c1", "Test Item", 100.0)
    assert "amount unavailable" in result
    assert "NOT sent" in result
    assert "INV-9" in result
