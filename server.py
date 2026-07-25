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
            return {"reply": f"Sorry, I hit an error: {e}"}
        _save_history(hf, hist[-MAX_HISTORY:])
    return {"reply": reply}


@app.get("/health")
def health():
    return {"ok": True}
