import json
from types import SimpleNamespace

import pytest

import casestatus


# --- fixtures: realistic shapes per the investigation doc ---

FORM_HTML = """
<html><body><form id="case_no_form">
<input type="hidden" name="scid" value="{scid}">
<input type="hidden" name="tok_9f2c1b7a8d3e4f5061728394a5b6c7d8e9f00112"
       value="d8e9f001129f2c1b7a8d3e4f5061728394a5b6c7">
<input type="hidden" name="sci_form_nonce" value="ab12cd34ef">
<input type="hidden" name="_wp_http_referer" value="/case-status-case-no/">
<input type="hidden" name="_form_time" value="1753500000">
<input type="hidden" name="_form_signature"
       value="{sig}">
<input type="hidden" name="es_ajax_request" value="1">
</form></body></html>
""".format(scid="a" * 40, sig="c" * 64)

RESULTS_HTML = """
<table class="results">
<thead><tr><th>Serial Number</th><th>Diary Number</th><th>Case Number</th>
<th>Petitioner Name</th><th>Respondent Name</th><th>Status</th><th>Action</th></tr></thead>
<tbody>
<tr data-diary-no="52650" data-diary-year="2023">
<td>1</td>
<td>52650/2023</td>
<td>SLP(C) No. 000001 / 2024 Registered on 02-01-2024</td>
<td>PARMESHWARDAS (D) THR. LRS.</td>
<td>CHAMPABAI AND ORS.</td>
<td>DISPOSED</td>
<td><a href="#" data-diary-no="52650" data-diary-year="2023">View</a></td>
</tr>
</tbody>
</table>
"""

SUCCESS_RESPONSE = {"success": True,
                    "data": {"pagination": False, "resultsHtml": RESULTS_HTML}}
# note: on failure, `data` is a DOUBLE-ENCODED json string (per investigation §2)
WRONG_CAPTCHA_RESPONSE = {"success": False,
                          "data": json.dumps({"message": "The captcha code entered was incorrect."})}
NOT_FOUND_RESPONSE = {"success": False,
                      "data": json.dumps({"message": "Nothing Found"})}

DETAIL_HTML = """
<div class="case-details"><table>
<tr><td>Diary No.</td><td>52650/2023</td></tr>
<tr><td>Filed on</td><td>22-12-2023</td></tr>
<tr><td>Section</td><td>X</td></tr>
<tr><td>Case No.</td><td>SLP(C) No. 000001/2024</td></tr>
<tr><td>Registered on</td><td>02-01-2024</td></tr>
<tr><td>CNR Number</td><td>SCIN010526502023</td></tr>
<tr><td>Present/Last Listed On</td>
    <td>10-05-2024 [HON'BLE MR. JUSTICE A. BENCHER and HON'BLE MR. JUSTICE C. DENCHER]</td></tr>
<tr><td>Status/Stage</td><td>DISPOSED</td></tr>
<tr><td>Disposal Type</td><td>DISMISSED (10-05-2024)</td></tr>
<tr><td>Category</td><td>0505-PROPERTY</td></tr>
<tr><td>Petitioner(s)</td><td>PARMESHWARDAS (D) THR. LRS.</td></tr>
<tr><td>Respondent(s)</td><td>CHAMPABAI AND ORS.</td></tr>
<tr><td>Pet. Advocate(s)</td><td>MR. P. COUNSEL</td></tr>
<tr><td>Resp. Advocate(s)</td><td>MS. R. COUNSEL</td></tr>
</table></div>
"""


# --- HTTP boundary fakes ---

class FakeResp:
    def __init__(self, json_data=None, content=b"", text=""):
        self._json = json_data
        self.content = content
        self.text = text
        self.status_code = 200

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        pass


class FakeSession:
    """Fakes the whole network boundary: form page GET, captcha GET, search POST."""

    def __init__(self, post_responses, detail_response=None):
        self.post_responses = list(post_responses)
        self.detail_response = detail_response
        self.captcha_fetches = 0
        self.form_fetches = 0
        self.posts = []
        self.headers = {}

    def get(self, url, **kw):
        if "_siwp_captcha" in url:
            self.captcha_fetches += 1
            return FakeResp(content=b"\x89PNG\r\n\x1a\nfakepngbytes")
        if "admin-ajax.php" in url or "get_case_details" in str(kw.get("params", "")):
            return FakeResp(json_data=self.detail_response)
        if "case-status" in url:
            self.form_fetches += 1
            return FakeResp(text=FORM_HTML)
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, data=None, **kw):
        self.posts.append(data)
        return FakeResp(json_data=self.post_responses.pop(0))


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(casestatus.time, "sleep", lambda s: None)


def _install(monkeypatch, fake):
    monkeypatch.setattr(casestatus, "_new_session", lambda: fake)
    return fake


# --- search tests ---

def test_success_parses_result_rows(monkeypatch, no_sleep):
    fake = _install(monkeypatch, FakeSession([SUCCESS_RESPONSE]))
    res = casestatus.sc_case_status(1, 1, 2024, solve=lambda png: 5)

    assert res["error"] is None
    assert res["found"] is True
    assert len(res["results"]) == 1
    row = res["results"][0]
    assert row["serial"] == "1"
    assert row["diary_no"] == "52650"
    assert row["diary_year"] == "2023"
    assert row["case_number"] == "SLP(C) No. 000001 / 2024"
    assert row["registered_on"] == "02-01-2024"
    assert row["petitioner"] == "PARMESHWARDAS (D) THR. LRS."
    assert row["respondent"] == "CHAMPABAI AND ORS."
    assert row["status"] == "DISPOSED"

    # the POST must mirror the site's own form exactly
    payload = fake.posts[0]
    assert payload["action"] == "get_case_status_case_no"
    assert payload["case_type"] == "1"
    assert payload["case_no"] == "1"
    assert payload["year"] == "2024"
    assert payload["siwp_captcha_value"] == "5"
    assert len(payload["scid"]) == 40 and payload["scid"] != "a" * 40  # fresh cid, not placeholder
    assert payload["sci_form_nonce"] == "ab12cd34ef"
    assert payload["_form_signature"] == "c" * 64
    assert payload["tok_9f2c1b7a8d3e4f5061728394a5b6c7d8e9f00112"] == \
        "d8e9f001129f2c1b7a8d3e4f5061728394a5b6c7"
    assert payload["es_ajax_request"] == "1"


def test_wrong_captcha_then_success_retries_with_fresh_captcha(monkeypatch, no_sleep):
    fake = _install(monkeypatch, FakeSession([WRONG_CAPTCHA_RESPONSE, SUCCESS_RESPONSE]))
    solves = []

    def solver(png):
        solves.append(png)
        return 5

    res = casestatus.sc_case_status(1, 1, 2024, solve=solver)
    assert res["found"] is True and len(res["results"]) == 1
    assert len(fake.posts) == 2            # retried the POST
    assert fake.captcha_fetches == 2       # fetched a NEW captcha for the retry
    assert len(solves) == 2                # re-solved
    scids = [p["scid"] for p in fake.posts]
    assert scids[0] != scids[1]            # fresh cid each attempt


def test_solver_parse_failure_fetches_fresh_captcha(monkeypatch, no_sleep):
    fake = _install(monkeypatch, FakeSession([SUCCESS_RESPONSE]))
    attempts = []

    def flaky(png):
        attempts.append(1)
        if len(attempts) == 1:
            raise ValueError("model said 'five'")
        return 5

    res = casestatus.sc_case_status(1, 1, 2024, solve=flaky)
    assert res["found"] is True
    assert fake.captcha_fetches == 2   # a fresh captcha after the parse failure
    assert len(fake.posts) == 1        # no POST was wasted on the unsolved captcha


def test_exhausted_captcha_retries(monkeypatch, no_sleep):
    fake = _install(monkeypatch, FakeSession([WRONG_CAPTCHA_RESPONSE] * 3))
    res = casestatus.sc_case_status(1, 1, 2024, max_captcha_retries=3, solve=lambda png: 5)
    assert res["found"] is False
    assert res["results"] == []
    assert "captcha" in res["error"].lower()
    assert "3" in res["error"]
    assert len(fake.posts) == 3


def test_not_found(monkeypatch, no_sleep):
    fake = _install(monkeypatch, FakeSession([NOT_FOUND_RESPONSE]))
    res = casestatus.sc_case_status(1, 999, 1990, solve=lambda png: 5)
    # design: not-found is a clean miss, not an error
    assert res == {"found": False, "results": [], "error": None}
    assert len(fake.posts) == 1  # no retry on a genuine not-found


def test_diary_search_uses_diary_action(monkeypatch, no_sleep):
    fake = _install(monkeypatch, FakeSession([SUCCESS_RESPONSE]))
    res = casestatus.sc_diary_status(52650, 2023, solve=lambda png: 5)
    assert res["found"] is True
    payload = fake.posts[0]
    assert payload["action"] == "get_case_status_diary_no"
    assert payload["diary_no"] == "52650"
    assert payload["year"] == "2023"
    assert "case_type" not in payload


def test_unexpected_json_shape_fails_loud(monkeypatch, no_sleep):
    _install(monkeypatch, FakeSession([{"weird": "shape"}]))
    res = casestatus.sc_case_status(1, 1, 2024, solve=lambda png: 5)
    assert res["found"] is False
    assert res["error"]  # clear error string, not a traceback


# --- default_solver (OpenAI vision) ---

def _fake_openai(monkeypatch, reply_text):
    created = {}

    class FakeOpenAI:
        def __init__(self, api_key):
            created["api_key"] = api_key
            completion = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=reply_text))])
            self.chat = SimpleNamespace(completions=SimpleNamespace(
                create=lambda **kw: created.update(kw) or completion))

    monkeypatch.setattr(casestatus.openai, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(casestatus, "get_cfg",
                        lambda: SimpleNamespace(key_openai="sk-test"))
    return created


def test_default_solver_parses_int(monkeypatch):
    created = _fake_openai(monkeypatch, "  5 ")
    assert casestatus.default_solver(b"\x89PNGfake") == 5
    assert created["api_key"] == "sk-test"
    assert created["model"] == "gpt-4.1-mini"
    content = created["messages"][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_default_solver_negative_answer(monkeypatch):
    _fake_openai(monkeypatch, "-2")
    assert casestatus.default_solver(b"png") == -2


def test_default_solver_non_int_reply_raises(monkeypatch):
    _fake_openai(monkeypatch, "The answer is five")
    with pytest.raises(ValueError):
        casestatus.default_solver(b"png")


# --- case details drill-down ---

def test_case_details_parses_documented_fields(monkeypatch):
    _install(monkeypatch, FakeSession([], detail_response={"success": True, "data": DETAIL_HTML}))
    d = casestatus.sc_case_details(52650, 2023)
    assert d["cnr"] == "SCIN010526502023"
    assert d["filed_on"] == "22-12-2023"
    assert d["section"] == "X"
    assert d["case_number"] == "SLP(C) No. 000001/2024"
    assert d["registered_on"] == "02-01-2024"
    assert d["last_listed_on"] == "10-05-2024"
    assert d["bench"] == ["HON'BLE MR. JUSTICE A. BENCHER", "HON'BLE MR. JUSTICE C. DENCHER"]
    assert d["status_stage"] == "DISPOSED"
    assert d["disposal"] == "DISMISSED (10-05-2024)"
    assert d["category"] == "0505-PROPERTY"
    assert d["petitioners"] == ["PARMESHWARDAS (D) THR. LRS."]
    assert d["respondents"] == ["CHAMPABAI AND ORS."]
    assert d["advocates"] == {"Pet. Advocate(s)": "MR. P. COUNSEL",
                              "Resp. Advocate(s)": "MS. R. COUNSEL"}
    assert d["raw_html"] == DETAIL_HTML


# label/format variants observed on the LIVE endpoint (2026-07-26): filing date and
# section embedded in the Diary Number row, "Disp.Type", judge names glued to "and"
DETAIL_HTML_LIVE_SHAPE = """
<table>
<tr><td>Diary Number</td><td>52650/2023 Filed on 15-12-2023 04:22 PM [ SECTION: I-B ]</td></tr>
<tr><td>Case Number</td><td>SLP(C) No. 000001 -  / 2024 Registered on 02-01-2024 (Verified On 02-01-2024)</td></tr>
<tr><td>CNR Number</td><td>SCIN010526502023</td></tr>
<tr><td>Present/Last Listed On</td>
    <td>05-01-2024 [ HON'BLE MS. JUSTICE HIMA KOHLIand HON'BLE MR. JUSTICE AHSANUDDIN AMANULLAH ]</td></tr>
<tr><td>Status/Stage</td><td>DISPOSED (Motion Hearing)</td></tr>
<tr><td>Disp.Type</td><td>Disposed Off</td></tr>
<tr><td>Petitioner(s)</td><td>1 PARMESHWARDAS S/O HARIRAM ASWANI</td></tr>
<tr><td>Respondent(s)</td><td>1 CHAMPABAI WD/O HAZARILAL GUPTA (DEAD) THROUGH LRS
1.1 HARIKISHAN</td></tr>
</table>
"""


def test_case_details_live_label_variants(monkeypatch):
    _install(monkeypatch, FakeSession([], detail_response={"success": True,
                                                           "data": DETAIL_HTML_LIVE_SHAPE}))
    d = casestatus.sc_case_details(52650, 2023)
    assert d["filed_on"] == "15-12-2023"
    assert d["section"] == "I-B"
    assert d["case_number"] == "SLP(C) No. 000001 -  / 2024"
    assert d["registered_on"] == "02-01-2024"
    assert d["last_listed_on"] == "05-01-2024"
    assert d["bench"] == ["HON'BLE MS. JUSTICE HIMA KOHLI",
                          "HON'BLE MR. JUSTICE AHSANUDDIN AMANULLAH"]
    assert d["disposal"] == "Disposed Off"
    assert d["petitioners"] == ["PARMESHWARDAS S/O HARIRAM ASWANI"]
    assert d["respondents"] == ["CHAMPABAI WD/O HAZARILAL GUPTA (DEAD) THROUGH LRS",
                                "HARIKISHAN"]


def test_case_details_fails_loud_on_shape_change(monkeypatch):
    _install(monkeypatch, FakeSession([], detail_response={"success": True,
                                                           "data": "<p>redesigned page</p>"}))
    with pytest.raises(ValueError, match="changed"):
        casestatus.sc_case_details(52650, 2023)


def test_case_details_unsuccessful_response(monkeypatch):
    _install(monkeypatch, FakeSession([], detail_response={"success": False, "data": "nope"}))
    with pytest.raises(ValueError):
        casestatus.sc_case_details(1, 2020)


# --- skills layer ---

def test_skill_formats_summary_and_detail(monkeypatch):
    import skills
    monkeypatch.setattr(casestatus, "sc_case_status",
                        lambda *a, **k: {"found": True, "error": None, "results": [{
                            "serial": "1", "diary_no": "52650", "diary_year": "2023",
                            "case_number": "SLP(C) No. 000001 / 2024",
                            "registered_on": "02-01-2024",
                            "petitioner": "PARMESHWARDAS", "respondent": "CHAMPABAI",
                            "status": "DISPOSED"}]})
    monkeypatch.setattr(casestatus, "sc_case_details",
                        lambda *a, **k: {"cnr": "SCIN010526502023", "filed_on": "22-12-2023",
                                         "section": "X", "case_number": "SLP(C) No. 000001/2024",
                                         "registered_on": "02-01-2024",
                                         "last_listed_on": "10-05-2024",
                                         "bench": ["J. A", "J. B"], "status_stage": "DISPOSED",
                                         "disposal": "DISMISSED", "category": "",
                                         "petitioners": [], "respondents": [],
                                         "advocates": {}, "raw_html": ""})
    out = skills.sc_case_status_lookup(1, 1, 2024)
    assert "PARMESHWARDAS" in out and "CHAMPABAI" in out
    assert "DISPOSED" in out
    assert "SCIN010526502023" in out
    assert "10-05-2024" in out


def test_skill_reports_captcha_error_cleanly(monkeypatch):
    import skills
    monkeypatch.setattr(casestatus, "sc_case_status",
                        lambda *a, **k: {"found": False, "results": [],
                                         "error": "could not solve captcha after 5 tries"})
    out = skills.sc_case_status_lookup(1, 1, 2024)
    assert "captcha" in out.lower()


def test_skill_not_found_message(monkeypatch):
    import skills
    monkeypatch.setattr(casestatus, "sc_diary_status",
                        lambda *a, **k: {"found": False, "results": [], "error": None})
    out = skills.sc_diary_status_lookup(99999, 1991)
    assert "no" in out.lower() and "99999" in out
