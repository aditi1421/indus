"""Where each answer's facts came from.

Two jobs. During a request, skills record the source behind every fact they
return, so the reply can carry a short tag. After the request, the record is
appended to a log, so "where did that come from" still works next week, long
after the answer has fallen out of the 60 item history window.

A ContextVar rather than a module global: FastAPI serves requests
concurrently, and one lawyer's sources must never leak into another's answer.
"""

import contextvars
import json
import os
from datetime import datetime, timezone
from pathlib import Path

CACHE = Path(os.getenv("INDUS_CACHE", "./.cache")).resolve()
LOG = CACHE / "provenance.jsonl"

# How far back to read when answering "where did that come from". The log is
# append only and unbounded; this keeps the read cheap.
MAX_SCAN_LINES = 500
MAX_ANSWER_CHARS = 300

_CURRENT = contextvars.ContextVar("provenance_record", default=None)


def start(chat="", sender="", question=""):
    """Begin recording for one request."""
    record_ = {"chat": chat, "sender": sender, "question": question, "sources": []}
    _CURRENT.set(record_)
    return record_


def record(source):
    """Note a source. A no-op outside a request, so skills stay callable in tests."""
    current = _CURRENT.get()
    if current is None:
        return
    if source not in current["sources"]:
        current["sources"].append(source)


def sources():
    current = _CURRENT.get()
    return list(current["sources"]) if current else []


def current_chat():
    current = _CURRENT.get()
    return current["chat"] if current else ""


def clear():
    _CURRENT.set(None)


def finish(answer=""):
    """Append this request's sources to the log and stop recording."""
    current = _CURRENT.get()
    if current is None:
        return None
    entry = dict(current)
    entry["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry["answer"] = (answer or "")[:MAX_ANSWER_CHARS]
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    clear()
    return entry


def recent(chat="", limit=5):
    """The most recent logged requests, newest last. Corrupt lines are skipped."""
    try:
        lines = LOG.read_text("utf-8").strip().splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines[-MAX_SCAN_LINES:]):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if chat and entry.get("chat") != chat:
            continue
        out.append(entry)
        if len(out) >= limit:
            break
    out.reverse()
    return out
