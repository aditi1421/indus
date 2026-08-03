"""The descriptions here are copied verbatim from the firm's live invoices,
typos included, because those typos are exactly what the parser has to survive.
"""

import billing

KAMAKSHI = ("Appearance and arguments on 03.08.2026 in WP(C) No. 469 of 2020 titled as "
            "M/S Kamakshi Ispat Ltd. Vs. Meghalaya Power Distribution Corporation Ltd. & Ors. "
            "before Meghalaya High Court at Shillong.")
DHAR = ("Appearance and arguments on 31.07.2026 in WP/C/348/2026 titiled as "
        "Dhar Construction Company Vs. Meghalaya Power Distribution Corporation Ltd. & Ors. "
        "pending before Meghalaya High Court at Shillong.")
WARJRI = ("Appearance and arguments on 28.07.20026 in WP(C) No. 414/2025 titled as "
          "Robert Warjri Vs. State of Meghalaya and 3 Ors.")


def test_parses_a_standard_description():
    case, title = billing.parse_matter(KAMAKSHI)

    assert case == "WP(C) No. 469 of 2020"
    assert title == "M/S Kamakshi Ispat Ltd. Vs. Meghalaya Power Distribution Corporation Ltd. & Ors."


def test_survives_the_titiled_typo_and_pending_before():
    case, title = billing.parse_matter(DHAR)

    assert case == "WP/C/348/2026"
    assert title.startswith("Dhar Construction Company Vs.")
    assert "pending" not in title


def test_parses_a_description_with_no_court_clause():
    case, title = billing.parse_matter(WARJRI)

    assert case == "WP(C) No. 414/2025"
    assert title == "Robert Warjri Vs. State of Meghalaya and 3 Ors."


def test_an_unparseable_description_is_admitted_not_guessed():
    assert billing.parse_matter("Professional fees for advice") == (None, None)


def test_clerkage_lines_are_not_mistaken_for_matters():
    assert billing.parse_matter("@ 10%") == (None, None)


class FakeZoho:
    """Two invoices in the house format, newest first."""

    def __init__(self, invoices=None, details=None):
        self._invoices = invoices if invoices is not None else [
            {"invoice_id": "b", "invoice_number": "INV-005011", "date": "2026-08-03"},
            {"invoice_id": "a", "invoice_number": "INV-005007", "date": "2026-07-31"},
        ]
        self._details = details or {
            "b": {"invoice_number": "INV-005011", "date": "2026-08-03", "line_items": [
                {"name": "Appearance & Arguments", "description": KAMAKSHI, "rate": 11000.0},
                {"name": "Clearkage", "description": "@ 10%", "rate": 1100.0}]},
            "a": {"invoice_number": "INV-005007", "date": "2026-07-31", "line_items": [
                {"name": "Appearance & Arguments", "description": DHAR, "rate": 11000.0},
                {"name": "Clearkage", "description": "@ 10%", "rate": 1100.0}]},
        }
        self.detail_calls = 0

    def invoices(self, customer_id="", limit=10):
        return self._invoices[:limit]

    def invoice(self, invoice_id):
        self.detail_calls += 1
        return self._details[invoice_id]


def test_profile_takes_the_rate_from_the_most_recent_appearance():
    prof = billing.profile(FakeZoho(), "c1")

    assert prof["rate"] == 11000.0


def test_profile_derives_the_clerkage_percentage():
    prof = billing.profile(FakeZoho(), "c1")

    assert prof["clerkage_pct"] == 10


def test_profile_lists_the_distinct_matters_with_exact_cause_titles():
    prof = billing.profile(FakeZoho(), "c1")

    cases = [m["case"] for m in prof["matters"]]
    assert "WP(C) No. 469 of 2020" in cases
    assert "WP/C/348/2026" in cases


def test_profile_does_not_repeat_a_matter_billed_twice():
    z = FakeZoho()
    z._details["a"]["line_items"][0]["description"] = KAMAKSHI  # same matter, both invoices

    prof = billing.profile(z, "c1")

    assert len(prof["matters"]) == 1


def test_profile_of_a_customer_with_no_history_is_empty_not_invented():
    prof = billing.profile(FakeZoho(invoices=[], details={}), "c1")

    assert prof["rate"] is None
    assert prof["matters"] == []


def test_profile_is_cached_so_a_billing_flow_does_not_refetch():
    billing.clear_cache()
    z = FakeZoho()

    billing.profile(z, "c1")
    first = z.detail_calls
    billing.profile(z, "c1")

    assert z.detail_calls == first


# --- duplicate guard ---


def test_an_existing_invoice_for_the_same_case_and_day_is_found():
    billing.clear_cache()

    found = billing.find_duplicate(FakeZoho(), "c1", "WP(C) No. 469 of 2020", "03.08.2026")

    assert found == "INV-005011"


def test_the_same_case_on_a_different_day_is_not_a_duplicate():
    billing.clear_cache()

    assert billing.find_duplicate(FakeZoho(), "c1", "WP(C) No. 469 of 2020", "04.08.2026") is None


def test_a_different_case_on_the_same_day_is_not_a_duplicate():
    billing.clear_cache()

    assert billing.find_duplicate(FakeZoho(), "c1", "CRP/22/2025", "03.08.2026") is None


# --- the skills lawyers actually reach ---


class FakeZohoWithContacts(FakeZoho):
    def __init__(self, contacts=None, **kw):
        super().__init__(**kw)
        self._contacts = contacts if contacts is not None else [
            {"contact_id": "c1", "contact_name": "Meghalaya Energy Corporation Limited"}]
        self.created = None

    def customers(self, search=""):
        return self._contacts

    def create_draft(self, customer_id, items):
        self.created = (customer_id, items)
        return {"invoice_id": "9", "invoice_number": "INV-9", "total": 12100.0, "status": "draft"}


def test_billing_profile_resolves_a_name_and_returns_rate_and_matters(monkeypatch):
    import skills
    z = FakeZohoWithContacts()
    monkeypatch.setattr(skills, "_zoho", lambda: z)

    out = skills.billing_profile("Meghalaya Energy")

    assert "Meghalaya Energy Corporation Limited" in out
    assert "11000" in out
    assert "WP(C) No. 469 of 2020" in out
    assert "M/S Kamakshi Ispat Ltd." in out


def test_billing_profile_asks_which_customer_when_the_name_is_ambiguous(monkeypatch):
    import skills
    z = FakeZohoWithContacts(contacts=[
        {"contact_id": "c1", "contact_name": "Meghalaya Energy Corporation Limited"},
        {"contact_id": "c2", "contact_name": "Meghalaya Legislative Assembly"}])
    monkeypatch.setattr(skills, "_zoho", lambda: z)

    out = skills.billing_profile("Meghalaya")

    assert "c1" in out and "c2" in out
    assert "which" in out.lower()


def test_billing_profile_says_plainly_when_there_is_no_history(monkeypatch):
    import skills
    z = FakeZohoWithContacts(invoices=[], details={})
    monkeypatch.setattr(skills, "_zoho", lambda: z)

    assert "no invoice history" in skills.billing_profile("Meghalaya Energy").lower()


def test_appearance_invoice_uses_the_standing_rate_when_no_fee_is_given(monkeypatch):
    import skills
    z = FakeZohoWithContacts()
    monkeypatch.setattr(skills, "_zoho", lambda: z)

    skills.zoho_create_appearance_invoice(
        "c1", "2026-08-10", "CRP/22/2025", "MePDCL Vs. Dhar Construction Company")

    fee_line, clerkage_line = z.created[1]
    assert fee_line["rate"] == 11000.0
    assert clerkage_line["rate"] == 1100.0


def test_appearance_invoice_refuses_to_raise_a_second_bill_for_the_same_hearing(monkeypatch):
    import skills
    z = FakeZohoWithContacts()
    monkeypatch.setattr(skills, "_zoho", lambda: z)

    out = skills.zoho_create_appearance_invoice(
        "c1", "2026-08-03", "WP(C) No. 469 of 2020", "M/S Kamakshi Ispat Ltd. Vs. MePDCL")

    assert z.created is None
    assert "INV-005011" in out


def test_appearance_invoice_needs_a_fee_when_the_customer_is_new(monkeypatch):
    import pytest
    import skills
    z = FakeZohoWithContacts(invoices=[], details={})
    monkeypatch.setattr(skills, "_zoho", lambda: z)

    with pytest.raises(ValueError, match="fee"):
        skills.zoho_create_appearance_invoice(
            "c9", "2026-08-10", "WP/C/1/2026", "A Vs. B")
