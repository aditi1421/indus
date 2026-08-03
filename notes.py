"""Durable notes the firm has explicitly taught the bot.

Deliberately separate from the rolling conversation history in server.py: that
window is trimmed to the last 60 items and is lost on a long enough gap, while
a taught fact ("MeECL means Meghalaya Energy Corporation Limited") has to
survive restarts indefinitely.

Nothing is written here automatically. A note exists only because someone asked
for it, which is what keeps stray group chatter out of the bot's mouth.
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

CACHE = Path(os.getenv("INDUS_CACHE", "./.cache")).resolve()
NOTES = CACHE / "notes.json"
MAX_BLOCK_CHARS = 4000

_LOCK = threading.Lock()


def load():
    """Every taught note, oldest first. A missing or corrupt file reads as empty."""
    try:
        data = json.loads(NOTES.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _save(items):
    NOTES.parent.mkdir(parents=True, exist_ok=True)
    tmp = NOTES.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False), "utf-8")
    tmp.replace(NOTES)


def _next_id(items):
    used = [int(n["id"]) for n in items if str(n.get("id", "")).isdigit()]
    return str(max(used, default=0) + 1)


def add(text, added_by=""):
    """Store a note and return it."""
    with _LOCK:
        items = load()
        note = {
            "id": _next_id(items),
            "text": text,
            "added_by": added_by,
            "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        items.append(note)
        _save(items)
    return note


def remove(note_id):
    """Drop one note. Returns False when the id isn't there."""
    with _LOCK:
        items = load()
        kept = [n for n in items if str(n.get("id")) != str(note_id)]
        if len(kept) == len(items):
            return False
        _save(kept)
    return True


def block(max_chars=MAX_BLOCK_CHARS):
    """The notes rendered for injection into the agent's instructions.

    Injected in full rather than retrieved by similarity: the volume is tens of
    notes, so injection is cheaper than a retrieval layer and far easier to
    audit, since what the bot knows is exactly what list_notes prints. Past the
    cap we keep the most recent notes and point the model at list_notes for the
    rest, so growth degrades instead of breaking.
    """
    items = load()
    if not items:
        return ""

    lines = []
    for n in items:
        who = f" (taught by {n['added_by']})" if n.get("added_by") else ""
        lines.append(f"• [{n['id']}] {n['text']}{who}")

    body = "\n".join(lines)
    if len(body) <= max_chars:
        return body

    kept, used = [], 0
    for line in reversed(lines):
        if used + len(line) + 1 > max_chars:
            break
        kept.append(line)
        used += len(line) + 1
    kept.reverse()
    return "\n".join(kept) + "\n(Older notes omitted; call list_notes for the full set.)"
