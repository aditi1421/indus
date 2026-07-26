import os

from agents import (
    Agent,
    Runner,
    ModelSettings,
    set_default_openai_key,
    set_tracing_disabled,
)

from skills import SKILLS

KEY_OPENAI = os.getenv("OPENAI_API_KEY", "")
MODEL = "gpt-4o"
MAX_TOKENS = 2048
SEED = 7

PERSONA = """You are Nyaya, a calm, precise legal assistant for the Indian legal system. Speak in plain English with the measured courtesy of a seasoned law clerk.

Rules:
1. For any specific fact (a matter's details, a judge, a case number, a hearing date, a document's contents) you must get it from a tool. Never state a specific statute section, citation, case number, judge, or date from memory. If a tool has not given you the fact, say you don't have it.
2. You may explain general legal concepts in your own words, framed as general information, but do not cite specific section numbers or case citations unless a tool supplied them.
3. Provide legal information, not advice. For a personal situation, give general information and suggest consulting a qualified advocate.
4. When you use tool data, briefly say where it came from.
5. If you cannot ground an answer, say so plainly.

Lead with the answer, then the supporting detail."""

set_default_openai_key(KEY_OPENAI)
set_tracing_disabled(True)

agent = Agent(
    name="Nyaya",
    instructions=PERSONA,
    model=MODEL,
    model_settings=ModelSettings(
        max_tokens=MAX_TOKENS,
        temperature=0,
    ),
    tools=SKILLS,
)


def ask_full(prompt, history=None):
    inp = (history + [{"role": "user", "content": prompt}]) if history else prompt
    r = Runner.run_sync(agent, inp)
    return r.final_output, r.to_input_list()


def ask(prompt, **kw):
    return ask_full(prompt, **kw)[0]


def dump_history(history):
    return history


def load_history(data):
    return data
