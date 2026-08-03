import json
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import provenance

HIST_DIR = Path(".cache/history")
HIST_DIR.mkdir(parents=True, exist_ok=True)
MAX_HISTORY = 60
# Item count is a poor proxy for cost: history is resent in full on every
# message. firm_register alone can return 100 spreadsheet rows, which would
# then ride along on the next 60 messages. Bound the total, and cut any single
# oversized item down before it is persisted.
MAX_HISTORY_CHARS = 12000
MAX_ITEM_CHARS = 2000
_TRUNCATED = "\n[...truncated to keep the resent history small]"
_LOCK = threading.Lock()

app = FastAPI(title="indus-clerk")


def _group() -> str:
    from config import get_cfg
    return get_cfg().group_jid


def _ask(text, hist):
    import agent
    from config import get_cfg
    agent.set_default_openai_key(get_cfg().key_openai)
    return agent.ask_full(text, history=hist or None)


def _load_history(hf):
    """Load history from file, tolerating corruption."""
    try:
        return json.loads(hf.read_text())
    except (OSError, json.JSONDecodeError):
        return []


def _save_history(hf, hist):
    """Save history atomically with temp file."""
    tmp = hf.with_suffix(".tmp")
    tmp.write_text(json.dumps(hist))
    tmp.replace(hf)


def _truncate_item(item):
    """Shrink one oversized item, leaving everything else untouched."""
    out = dict(item)
    for key in ("output", "content"):
        value = out.get(key)
        if isinstance(value, str) and len(value) > MAX_ITEM_CHARS:
            out[key] = value[:MAX_ITEM_CHARS] + _TRUNCATED
    return out


def _trim_history(hist, max_items=MAX_HISTORY, max_chars=MAX_HISTORY_CHARS):
    """Slice to the last max_items, then drop leading items that aren't a
    plain message.

    `to_input_list()` can include tool/function items (type keys like
    "function_call"/"function_call_output", no usable "role"). A raw
    positional slice can strand one of these at the front (e.g. a
    function_call_output whose matching function_call fell just outside the
    window) or leave a trailing unanswered function_call. Either poisons the
    persisted history: the Responses API 400s on it forever. Be defensive —
    anything without a role in our known set is treated as droppable.
    """
    trimmed = [_truncate_item(i) for i in (hist[-max_items:] if max_items else hist)]

    if max_chars:
        kept, used = [], 0
        for item in reversed(trimmed):
            size = len(json.dumps(item))
            if kept and used + size > max_chars:
                break
            kept.append(item)
            used += size
        kept.reverse()
        trimmed = kept

    while trimmed and trimmed[0].get("role") not in ("user", "assistant", "system", "developer"):
        trimmed.pop(0)
    return trimmed


_APOLOGY = "Sorry, I hit an internal error — please try again."


class ChatIn(BaseModel):
    chat: str
    sender: str = ""
    text: str


@app.post("/chat")
def chat(m: ChatIn):
    if m.chat != _group():
        raise HTTPException(403, "not the firm group")
    prompt = f"{m.sender}: {m.text}" if m.sender else m.text
    with _LOCK:
        hf = HIST_DIR / "group.json"
        hist = _load_history(hf) if hf.is_file() else []
        provenance.start(chat=m.chat, sender=m.sender, question=m.text)
        try:
            reply, hist = _ask(prompt, hist)
        except Exception as e:
            print(f"[server] _ask failed: {e!r}")
            if not hist:
                provenance.clear()
                return {"reply": _APOLOGY}
            # The persisted history itself may be the poison (a stranded
            # tool item that makes the Responses API 400 on every request).
            # Retry once with a clean slate instead of wedging forever.
            try:
                reply, hist = _ask(prompt, [])
            except Exception as e2:
                print(f"[server] retry with empty history also failed: {e2!r}")
                provenance.clear()
                return {"reply": _APOLOGY}
        provenance.finish(reply)
        _save_history(hf, _trim_history(hist))
    return {"reply": reply}


@app.get("/health")
def health():
    return {"ok": True}
