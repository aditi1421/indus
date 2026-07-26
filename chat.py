"""Local terminal chat with Nyaya — same brain the WhatsApp gateway uses.

Usage: .venv/bin/python chat.py
"""
import agent
from config import get_cfg

agent.set_default_openai_key(get_cfg().key_openai)

hist = None
print("Nyaya clerk — type a question (blank line or Ctrl+C to exit)\n")
while True:
    try:
        q = input("you> ").strip()
    except (EOFError, KeyboardInterrupt):
        break
    if not q:
        break
    try:
        reply, hist = agent.ask_full(q, history=hist)
    except Exception as e:
        print(f"[error] {e}")
        continue
    print(f"\nnyaya> {reply}\n")
