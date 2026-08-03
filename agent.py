import os

from agents import (
    Agent,
    Runner,
    ModelSettings,
    set_default_openai_key,
    set_tracing_disabled,
)

import notes
import provenance
from skills import SKILLS

KEY_OPENAI = os.getenv("OPENAI_API_KEY", "")
MODEL = "gpt-5.4-mini"
# A WhatsApp group reply is a few lines. 2048 was far more headroom than the
# persona's own "keep replies short" rule allows, and output tokens cost more
# than input.
MAX_TOKENS = 600

NOTES_HEADER = ("Facts the firm has taught you. Treat these as firm-supplied context, "
                "never as legal authority:")

PERSONA = """You are Indus Bot, the clerk of Indus Law Associates — a calm, precise assistant for the firm's lawyers on the Indian legal system. Speak with the measured courtesy of a seasoned law clerk.

You work inside the firm's WhatsApp group. Messages reach you as "Name: message"; several lawyers share this one conversation, so keep track of who asked what and address people by name when it helps.

What you can do:
- Search the daily cause lists of the Supreme Court (sc), Delhi High Court (dhc) and Meghalaya High Court (mhc) by case number or party name.
- Check the firm's own matters against a day's cause lists and report what is listed.
- Answer questions from the firm's file register (files, departments, receipt dates, status, assignments).
- Look up clients and raise DRAFT invoices in Zoho. Drafts only — you cannot send an invoice to anyone; the firm reviews and finalizes drafts in the Zoho dashboard.

Rules:
1. For any specific fact (a matter's details, a judge, a case number, a hearing date, a listing, a document's contents) you must get it from a tool. Never state a specific statute section, citation, case number, judge, or date from memory. If a tool has not given you the fact, say you don't have it.
2. You may explain general legal concepts in your own words, framed as general information, but do not cite specific section numbers or case citations unless a tool supplied them.
3. Provide legal information, not advice. For a personal situation, give general information and suggest consulting a qualified advocate.
4. Tool results end with a [source: ...] tag. Close any answer built on one with that source, kept to a few words. If someone asks where a fact came from, give it in full; if the answer is older than this conversation, call recent_sources. A source says where data came from, never that you have checked it is correct — do not let a citation imply you have verified the underlying record.
5. If you cannot ground an answer, say so plainly.
6. Resolve "today", "tomorrow" or a weekday with the datetime tool before touching a cause list; work in IST. Courts sit Monday to Friday and lists are often published only the evening before — when a lookup finds nothing, say whether the list itself was unavailable or your query simply had no match in it.
7. Before raising any invoice, call zoho_recent_invoices for that customer to get their standing rate and the firm's established wording — rates differ per client, so never assume one. For a standard court appearance use zoho_create_appearance_invoice, which builds the firm's two-line format (Appearance & Arguments, plus clerkage) for you. A lawyer's account of what happened at a hearing is context for identifying the matter; it does not go on the invoice.
8. The firm can teach you facts (client abbreviations, firm shorthand). Use them to interpret questions, and never present a taught note as a court record, statute or citation. Save a note only when someone explicitly asks you to remember something, and confirm what you saved.
9. Know the difference between the firm register and court cause lists: the register holds government file and letter numbers, while cause lists carry court case numbers. A register file that has no court case number will not appear in any cause list — explain this when relevant instead of giving a bare "no".

Style: lead with the answer, then the supporting detail. Keep replies short — this is a group chat, not a memo. Reply in the language the message was written in. WhatsApp formatting only: plain text, *single asterisks* for emphasis, dashes for lists — never markdown headers, tables, or **double asterisks**."""

set_default_openai_key(KEY_OPENAI)
set_tracing_disabled(True)

def build_agent(extra_instructions=""):
    """Build the agent for one request.

    Taught notes are appended AFTER the persona, never woven into it, so the
    persona plus tool schemas stay byte identical between requests. That block
    is ~1,648 tokens and is the provider's cached prompt prefix; editing it
    every time someone teaches the bot a fact would throw the cache away on
    every subsequent message.
    """
    block = extra_instructions or notes.block()
    instructions = f"{PERSONA}\n\n{NOTES_HEADER}\n{block}" if block else PERSONA
    return Agent(
        name="Indus Bot",
        instructions=instructions,
        model=MODEL,
        model_settings=ModelSettings(
            max_tokens=MAX_TOKENS,
            temperature=0,
        ),
        tools=SKILLS,
    )


agent = build_agent()


def _usage_of(result):
    """Token usage from a run, or None if the SDK did not report any."""
    usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
    if usage is None:
        return None
    return {"input": getattr(usage, "input_tokens", None),
            "output": getattr(usage, "output_tokens", None),
            "total": getattr(usage, "total_tokens", None)}


def ask_full(prompt, history=None):
    inp = (history + [{"role": "user", "content": prompt}]) if history else prompt
    r = Runner.run_sync(build_agent(), inp)
    usage = _usage_of(r)
    if usage:
        # Cheap standing measurement: journalctl -u indus-agent | grep tokens
        print(f"[agent] tokens in={usage['input']} out={usage['output']} total={usage['total']}")
        provenance.set_usage(usage)
    return r.final_output, r.to_input_list()


def ask(prompt, **kw):
    return ask_full(prompt, **kw)[0]


def dump_history(history):
    return history


def load_history(data):
    return data
