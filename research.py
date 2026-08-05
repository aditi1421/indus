"""Legal web research via the Exa search API.

For research questions ("recent SC judgments on anticipatory bail"), not for
anything the firm's own scrapers answer authoritatively: cause lists, case
status and billing have their own skills, and those read the courts' and
Zoho's own systems directly.

Searches are restricted to an allowlist of legal sources, so a stray blog
post is never cited to a lawyer as authority. The allowlist is deliberate:
loosen it only if the firm reports real misses.

The API key is optional in config: without it the agent boots fine and this
module raises a clear error only when a search is actually attempted.
"""

import time

import requests

from config import get_cfg

API = "https://api.exa.ai/search"
TIMEOUT = 30
CACHE_TTL_SECONDS = 24 * 3600
NUM_RESULTS = 5

LEGAL_DOMAINS = [
    "indiankanoon.org",
    "livelaw.in",
    "barandbench.com",
    "verdictum.in",
    "scconline.com",
    "scobserver.in",
    "main.sci.gov.in",
    "delhihighcourt.nic.in",
    "meghalayahighcourt.nic.in",
]

_CACHE = {}


def clear_cache():
    _CACHE.clear()


def _payload(query, num_results):
    return {
        "query": query,
        "type": "auto",
        "numResults": num_results,
        "includeDomains": LEGAL_DOMAINS,
        "contents": {"highlights": {"numSentences": 2, "highlightsPerUrl": 1}},
    }


def _fetch(query, num_results):
    key = get_cfg().key_exa
    if not key:
        raise ValueError(
            "Exa API key not configured: set SSM parameter /apps/courts/key_exa."
        )
    response = requests.post(
        API, json=_payload(query, num_results),
        headers={"x-api-key": key}, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def parse_results(data):
    """Result rows out of Exa's /search reply, one compact dict per hit."""
    rows = []
    for r in (data or {}).get("results") or []:
        rows.append({
            "title": (r.get("title") or "").strip(),
            "url": r.get("url") or "",
            "published": (r.get("publishedDate") or "")[:10],
            "highlight": " ".join((r.get("highlights") or [""])[0].split()),
        })
    return rows


def search(query, *, num_results=NUM_RESULTS, fetch=None):
    """{"found": bool, "results": [...], "error": str|None}, cached for a day.

    An API failure is reported as an error, never as an empty result, so a
    lawyer is never told "nothing on this" when the truth is "search is down".
    Failures are not cached: the next question retries.
    """
    key = " ".join(query.split()).lower()
    hit = _CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]

    fetch = fetch or _fetch
    try:
        rows = parse_results(fetch(query, num_results))
    except Exception as e:  # network, auth, or a malformed reply
        return {"found": False, "results": [], "error": f"{type(e).__name__}: {e}"}
    result = {"found": bool(rows), "results": rows, "error": None}
    _CACHE[key] = (now, result)
    return result
