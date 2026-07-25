import zoho


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
