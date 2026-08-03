"""Meghalaya High Court case status, via the court's own Drupal site.

Notably cheaper than the Supreme Court equivalent in casestatus.py. That one
downloads a captcha image and spends a vision call per attempt, with retries.
This site's captcha endpoint hands back the question as plain text
("99 + 2"), so the answer is arithmetic done here: no model call, no tokens,
and it cannot misread a digit.

The search is Drupal AJAX. A plain form POST returns the page with no results
and no error, which is a quiet way to look broken; the real request adds
?ajax_form=1&_wrapper_format=drupal_ajax and an XMLHttpRequest header, and the
reply is JSON commands carrying the results table.

The eCourts route (hcservices.ecourts.gov.in) serves the same data behind a
distorted alphanumeric captcha instead. This door is the cheap one.
"""

import re
import time

import requests
from bs4 import BeautifulSoup

BASE = "http://meghalayahighcourt.nic.in"
FORM_PATH = "/case-status"
AJAX_QUERY = "?ajax_form=1&_wrapper_format=drupal_ajax"
CAPTCHA_PATH = "/math-captcha/image"

UA = {"User-Agent": "Mozilla/5.0 (Macintosh) indus-clerk/1.0"}
TIMEOUT = 30
MAX_CAPTCHA_RETRIES = 3
CACHE_TTL_SECONDS = 24 * 3600

_QUESTION = re.compile(r"^\s*(\d+)\s*([+\-x*×])\s*(\d+)\s*$", re.IGNORECASE)
# Every response re-renders a fresh captcha, so its markup rides along with the
# results. Never mistake "7 + 5 equals" for a case.
_CAPTCHA_NOISE = re.compile(r"^\s*\d+\s*[+\-x*×]\s*\d+\s*(equals)?\s*$", re.IGNORECASE)

_CACHE = {}


def clear_cache():
    _CACHE.clear()


def solve_question(question):
    """'99 + 2' -> 101. Raises when the question isn't arithmetic we recognise."""
    m = _QUESTION.match(question or "")
    if not m:
        raise ValueError(f"unreadable captcha question: {question!r}")
    left, op, right = int(m.group(1)), m.group(2), int(m.group(3))
    if op == "+":
        return left + right
    if op == "-":
        return left - right
    return left * right


def parse_results(commands):
    """Case rows out of the Drupal AJAX command list."""
    rows = []
    for command in commands or []:
        data = command.get("data") if isinstance(command, dict) else None
        if not isinstance(data, str) or "<table" not in data:
            continue
        soup = BeautifulSoup(data, "html.parser")
        for table in soup.find_all("table"):
            for tr in table.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
                if len(cells) < 4:
                    continue  # header rows carry <th>, not <td>
                if _CAPTCHA_NOISE.match(cells[0]):
                    continue
                rows.append({"case_no": cells[0], "petitioner": cells[1],
                             "respondent": cells[2], "status": cells[3]})
    return rows


def _fetch(case_type, number, year):
    """One live search: load the form, answer the captcha, post via AJAX."""
    session = requests.Session()
    session.headers.update(UA)
    page = session.get(BASE + FORM_PATH, timeout=TIMEOUT).text
    soup = BeautifulSoup(page, "html.parser")
    payload = {i.get("name"): (i.get("value") or "")
               for i in soup.find_all("input")
               if i.get("type") == "hidden" and i.get("name")}

    question = session.get(f"{BASE}{CAPTCHA_PATH}?{int(time.time() * 1000)}",
                           timeout=TIMEOUT).json().get("question")
    payload.update({
        "case_type": case_type, "reg_no": str(number), "year": str(year),
        "qry": "case", "captcha[captcha_answer]": str(solve_question(question)),
        "op": "🔍 Search", "_drupal_ajax": "1",
        "_triggering_element_name": "op", "_triggering_element_value": "🔍 Search",
    })
    response = session.post(
        BASE + FORM_PATH + AJAX_QUERY, data=payload,
        headers={"X-Requested-With": "XMLHttpRequest",
                 "Accept": "application/json, text/javascript, */*; q=0.01"},
        timeout=TIMEOUT + 15)
    response.raise_for_status()
    return response.json()


def mhc_case_status(case_type, number, year, *, fetch=None,
                    max_retries=MAX_CAPTCHA_RETRIES):
    """{"found": bool, "results": [...], "error": str|None}, cached for a day.

    A portal failure is reported as an error, never as an empty result, so a
    lawyer is never told "no such case" when the truth is "the site was down".
    """
    key = (case_type, str(number), str(year))
    hit = _CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]

    fetch = fetch or _fetch
    last_error = None
    for _ in range(max(1, max_retries)):
        try:
            rows = parse_results(fetch(case_type, number, year))
        except Exception as e:  # network, JSON, or an unreadable captcha
            last_error = f"{type(e).__name__}: {e}"
            continue
        result = {"found": bool(rows), "results": rows, "error": None}
        _CACHE[key] = (now, result)
        return result

    return {"found": False, "results": [], "error": last_error or "lookup failed"}
