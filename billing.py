"""What the bot needs to know from billing history, and nothing more.

Sending whole past invoices to the model was the wrong shape: the firm's
template never varies, so it was spending thousands of tokens re-teaching the
model something already hardcoded in skills.py. Only two things actually have
to come from history: the customer's current rate, and the exact case number
and cause title of the matter.

That second one is the valuable part. The bills already carry the precise
title ("WP/C/348/2026 titled as Dhar Construction Company Vs. ..."), so a new
invoice can reuse that string instead of a lawyer's shorthand being retyped
into a mangled case number.
"""

import re
import time

# Invoice descriptions are hand written and irregular: "titiled" for "titled",
# "pending before" as often as "before", and some have no court clause at all.
# Two tolerant patterns beat one clever one, and anything that matches neither
# is reported as unparsed rather than guessed at — this is billing.
_WITH_COURT = re.compile(
    r"\bin\s+(?P<case>.+?)\s+tit\w*ed\s+as\s+(?P<title>.+?)\s+(?:pending\s+)?before\b",
    re.IGNORECASE | re.DOTALL)
_WITHOUT_COURT = re.compile(
    r"\bin\s+(?P<case>.+?)\s+tit\w*ed\s+as\s+(?P<title>.+?)\s*$",
    re.IGNORECASE | re.DOTALL)

APPEARANCE_HINT = "appearance"
CLERKAGE_HINT = "clerkage"
CLERKAGE_TYPO_HINT = "clearkage"

SAMPLE_SIZE = 10
MAX_MATTERS = 8
CACHE_TTL_SECONDS = 300

_CACHE = {}


def clear_cache():
    _CACHE.clear()


def parse_matter(description):
    """(case number, cause title), or (None, None) when the line isn't a matter."""
    text = (description or "").strip()
    if not text:
        return (None, None)
    for pattern in (_WITH_COURT, _WITHOUT_COURT):
        m = pattern.search(text)
        if m:
            case = m.group("case").strip().rstrip(",")
            title = m.group("title").strip()
            if case and title:
                return (case, title)
    return (None, None)


def _is_appearance(line):
    return APPEARANCE_HINT in (line.get("name") or "").lower()


def _is_clerkage(line):
    name = (line.get("name") or "").lower()
    return CLERKAGE_HINT in name or CLERKAGE_TYPO_HINT in name


def _details(zoho_client, customer_id, sample=SAMPLE_SIZE):
    """Recent invoices with line items, newest first, cached briefly.

    Billing a matter touches this twice (profile, then the duplicate check), and
    each detail is its own API call, so a short cache keeps one flow to one fetch.
    """
    key = (customer_id, sample)
    hit = _CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    out = [zoho_client.invoice(i["invoice_id"])
           for i in zoho_client.invoices(customer_id=customer_id, limit=sample)]
    _CACHE[key] = (now, out)
    return out


def profile(zoho_client, customer_id, sample=SAMPLE_SIZE):
    """Standing rate, clerkage percentage and the distinct matters billed before."""
    invoices = _details(zoho_client, customer_id, sample)

    rate = None
    clerkage_pct = None
    matters = []
    seen = set()
    unparsed = []

    for inv in invoices:  # newest first, so the first rate we see is the current one
        lines = inv.get("line_items", [])
        appearance = next((line for line in lines if _is_appearance(line)), None)
        clerkage = next((line for line in lines if _is_clerkage(line)), None)

        if appearance and rate is None:
            rate = appearance.get("rate")
            if clerkage and rate:
                clerkage_pct = round(clerkage.get("rate", 0) / rate * 100)

        for line in lines:
            if _is_clerkage(line):
                continue
            case, title = parse_matter(line.get("description"))
            if case and case not in seen:
                seen.add(case)
                matters.append({"case": case, "title": title,
                                "last_billed": inv.get("date", "")})
            elif not case and line.get("description") and _is_appearance(line):
                unparsed.append(line["description"])

    return {"rate": rate, "clerkage_pct": clerkage_pct,
            "matters": matters[:MAX_MATTERS], "unparsed": unparsed[:3],
            "invoice_numbers": [i.get("invoice_number", "?") for i in invoices]}


def find_duplicate(zoho_client, customer_id, case_number, hearing_ddmmyyyy,
                   sample=SAMPLE_SIZE):
    """The invoice number of an existing bill for this matter on this day, if any.

    A real one was found in production: INV-005011 for Kamakshi Ispat dated the
    same day nobody was sure who had raised. Cheaper to check than to unpick.
    """
    for inv in _details(zoho_client, customer_id, sample):
        for line in inv.get("line_items", []):
            description = line.get("description") or ""
            if hearing_ddmmyyyy in description and case_number in description:
                return inv.get("invoice_number")
    return None
