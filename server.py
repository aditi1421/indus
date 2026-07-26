import json
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

HIST_DIR = Path(".cache/history")
HIST_DIR.mkdir(parents=True, exist_ok=True)
MAX_HISTORY = 60
_LOCK = threading.Lock()

app = FastAPI(title="nyaya-clerk")


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


def _trim_history(hist, max_items=MAX_HISTORY):
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
    trimmed = list(hist[-max_items:] if max_items else hist)
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
        try:
            reply, hist = _ask(prompt, hist)
        except Exception as e:
            print(f"[server] _ask failed: {e!r}")
            if not hist:
                return {"reply": _APOLOGY}
            # The persisted history itself may be the poison (a stranded
            # tool item that makes the Responses API 400 on every request).
            # Retry once with a clean slate instead of wedging forever.
            try:
                reply, hist = _ask(prompt, [])
            except Exception as e2:
                print(f"[server] retry with empty history also failed: {e2!r}")
                return {"reply": _APOLOGY}
        _save_history(hf, _trim_history(hist))
    return {"reply": reply}


@app.get("/health")
def health():
    return {"ok": True}
