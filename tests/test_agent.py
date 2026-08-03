import agent
import notes


def test_build_agent_without_notes_leaves_the_persona_byte_identical(tmp_path, monkeypatch):
    """The persona plus tool block is the cached prompt prefix. With nothing
    taught, it must not gain so much as a newline, or caching is lost."""
    monkeypatch.setattr(notes, "NOTES", tmp_path / "notes.json")

    built = agent.build_agent()

    assert built.instructions == agent.PERSONA


def test_build_agent_appends_taught_notes_after_the_persona(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "NOTES", tmp_path / "notes.json")
    notes.add("MeECL means Meghalaya Energy Corporation Limited")

    built = agent.build_agent()

    assert built.instructions.startswith(agent.PERSONA)
    assert "MeECL means Meghalaya Energy Corporation Limited" in built.instructions


def test_build_agent_keeps_the_tool_surface(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "NOTES", tmp_path / "notes.json")

    built = agent.build_agent()

    assert {t.name for t in built.tools} >= {"current_datetime", "zoho_find_customer"}


def test_reply_length_is_capped_for_a_group_chat():
    """2048 output tokens is far longer than anything that belongs in WhatsApp,
    and output tokens are the expensive kind."""
    assert agent.MAX_TOKENS <= 800
