"""Guards on what gets resent to the model on every single message.

Measured 2026-08-03: persona 643 tokens plus tool schemas 1,004 tokens is fixed
overhead on every request. History is the part that can grow without anyone
noticing, because it is resent in full each turn.
"""

import json

import agent
import casestatus
import provenance
import server


def _msg(role, text):
    return {"role": role, "content": text}


def test_history_is_bounded_by_characters_not_just_item_count():
    """60 small messages and 60 huge ones are very different bills."""
    fat = [_msg("user", "x" * 5000) for _ in range(20)]

    trimmed = server._trim_history(fat)

    assert sum(len(json.dumps(i)) for i in trimmed) <= server.MAX_HISTORY_CHARS


def test_a_giant_tool_result_is_truncated_before_it_is_persisted():
    """firm_register can return 100 spreadsheet rows. Persisted whole, it is
    resent on every message until it scrolls out 60 items later."""
    history = [_msg("user", "show me the register"),
               {"type": "function_call_output", "call_id": "1", "output": "row\n" * 10000}]

    trimmed = server._trim_history(history)

    assert len(trimmed[-1]["output"]) <= server.MAX_ITEM_CHARS + 100
    assert "truncated" in trimmed[-1]["output"]


def test_a_short_history_is_left_exactly_as_it_was():
    history = [_msg("user", "what is listed tomorrow"), _msg("assistant", "Two matters.")]

    assert server._trim_history(history) == history


def test_recent_messages_are_the_ones_kept():
    history = [_msg("user", f"message {i} " + "x" * 3000) for i in range(20)]

    trimmed = server._trim_history(history)

    assert "message 19" in json.dumps(trimmed[-1])


# --- Supreme Court lookups are slow and cost a vision call per captcha ---


def test_sc_case_status_is_served_from_cache_on_a_repeat_lookup(monkeypatch):
    calls = []

    def fake_run(*a, **kw):
        calls.append(a)
        return {"found": True, "results": [{"case_number": "SLP(C) 1/2024"}]}

    casestatus.clear_cache()
    monkeypatch.setattr(casestatus, "_sc_case_status_uncached", fake_run)

    first = casestatus.sc_case_status(1, 1, 2024)
    second = casestatus.sc_case_status(1, 1, 2024)

    assert first == second
    assert len(calls) == 1


def test_a_failed_sc_lookup_is_not_cached(monkeypatch):
    """Caching an outage would keep serving it after the portal recovers."""
    results = [{"error": "captcha solver unavailable"}, {"found": True, "results": []}]

    casestatus.clear_cache()
    monkeypatch.setattr(casestatus, "_sc_case_status_uncached", lambda *a, **kw: results.pop(0))

    casestatus.sc_case_status(1, 1, 2024)
    second = casestatus.sc_case_status(1, 1, 2024)

    assert second.get("found") is True


# --- know what a question actually costs ---


def test_token_usage_is_read_off_a_run_result():
    class Usage:
        input_tokens, output_tokens, total_tokens = 3600, 180, 3780

    class Ctx:
        usage = Usage()

    class Result:
        context_wrapper = Ctx()

    assert agent._usage_of(Result()) == {"input": 3600, "output": 180, "total": 3780}


def test_missing_usage_is_not_an_error():
    assert agent._usage_of(object()) is None


def test_usage_is_written_into_the_provenance_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(provenance, "LOG", tmp_path / "provenance.jsonl")
    provenance.start(chat="group@g.us", sender="Aditi", question="what is listed")

    provenance.set_usage({"input": 3600, "output": 180, "total": 3780})
    entry = provenance.finish("Two matters.")

    assert entry["usage"]["total"] == 3780
