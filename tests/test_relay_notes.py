"""The owner's notes for relays: files named in config.toml, handed to relays with the guide.

A relay is a caller that isn't a Claude Code session: a Claude app conversation, by its MCP session
or with none. The notes come with the guide (the first result, the guide tool) and are read from
disk each time, so an edit to them reaches the next relay without a restart.
"""

import json

from claude_voice import relay_notes
from claude_voice.server import INSTRUCTIONS, result_notes


class Request:
    def __init__(self, headers):
        self.headers = headers


class Ctx:
    def __init__(self, headers=None, name="list_folders"):
        self.method = "tools/call"
        self.params = {"name": name, "arguments": {}}
        self.request = Request(headers or {})


async def answer(ctx):
    return {"structuredContent": {"folders": []}, "content": []}


def notes_file(tmp_path, *paths):
    config = tmp_path / "config.toml"
    config.write_text("relay_notes = [" + ", ".join(json.dumps(str(p)) for p in paths) + "]\n", encoding="utf-8")
    return config


def test_the_notes_are_named_in_config_toml(tmp_path):
    role, setup = tmp_path / "Relay.md", tmp_path / "Coordinator setup.md"
    assert relay_notes.configured(notes_file(tmp_path, role, setup)) == [role, setup]


def test_without_a_config_file_or_key_there_are_none(tmp_path):
    assert relay_notes.configured(None) == []
    plain = tmp_path / "config.toml"
    plain.write_text("[budgets]\nclaude_app = 180\n", encoding="utf-8")
    assert relay_notes.configured(plain) == []
    assert relay_notes.text([]) is None


def test_each_note_comes_under_its_name_without_its_frontmatter(tmp_path):
    role = tmp_path / "Relay.md"
    role.write_bytes(b"---\ndescription: how the relay works\n---\nFor you, the relay.\r\n\r\n1. Who does what.\n")
    setup = tmp_path / "Coordinator setup.md"
    setup.write_text("Who does what in the setup.\n")
    said = relay_notes.text([role, setup])
    assert said is not None
    assert "description:" not in said and "\r" not in said
    assert said.index("# Relay\nFor you, the relay.\n\n1. Who does what.") < said.index(
        "# Coordinator setup\nWho does what in the setup."
    )


def test_a_note_that_cannot_be_read_is_named_and_the_rest_still_comes(tmp_path):
    setup = tmp_path / "Coordinator setup.md"
    setup.write_text("Who does what.\n")
    said = relay_notes.text([tmp_path / "Relay.md", setup])
    assert said is not None
    assert "# Relay\n" in said and "couldn't read" in said and "tell the owner" in said
    assert "Who does what." in said


def test_a_long_note_is_cut(tmp_path):
    role = tmp_path / "Relay.md"
    role.write_text("Z" * (relay_notes.NOTE_BYTES + 500))
    said = relay_notes.text([role])
    assert said is not None and said.count("Z") == relay_notes.NOTE_BYTES


async def test_a_claude_app_conversation_gets_the_notes_with_the_guide(tmp_path):
    note = result_notes(claude_code_transcript=lambda: None, relay_notes=lambda: "the relay's notes")
    first = await note(Ctx({"mcp-session-id": "chat-1"}), answer)
    second = await note(Ctx({"mcp-session-id": "chat-1"}), answer)
    assert first["structuredContent"]["guide"] == INSTRUCTIONS
    assert first["structuredContent"]["relay_notes"] == "the relay's notes"
    assert list(first["structuredContent"]).index("relay_notes") < list(first["structuredContent"]).index("folders")
    assert "relay_notes" not in second["structuredContent"]


async def test_a_relay_calling_guide_gets_the_notes_again(tmp_path):
    # After a compaction, a relay in the Claude app calls guide again (a line in the owner's claude.ai memory).
    note = result_notes(claude_code_transcript=lambda: None, relay_notes=lambda: "the relay's notes")
    await note(Ctx({"mcp-session-id": "chat-1"}), answer)
    again = await note(Ctx({"mcp-session-id": "chat-1"}, name="guide"), answer)
    typed = await note(Ctx(name="guide"), answer)  # a typed chat, with no MCP session
    assert again["structuredContent"]["relay_notes"] == "the relay's notes"
    assert typed["structuredContent"]["relay_notes"] == "the relay's notes"


async def test_a_relay_gets_the_notes_for_relays_with_the_guide_a_claude_code_session_does_not(tmp_path):
    transcript = tmp_path / "desk-1.jsonl"
    transcript.write_text(json.dumps({"type": "user"}) + "\n", encoding="utf-8")
    note = result_notes(claude_code_transcript=lambda: transcript, relay_notes=lambda: "the relay's notes")
    first = await note(Ctx(), answer)
    asked = await note(Ctx(name="guide"), answer)
    assert first["structuredContent"]["guide"] == INSTRUCTIONS
    assert "relay_notes" not in first["structuredContent"]
    assert "relay_notes" not in asked["structuredContent"]


async def test_no_notes_configured_adds_nothing(tmp_path):
    note = result_notes(claude_code_transcript=lambda: None, relay_notes=lambda: None)
    first = await note(Ctx({"mcp-session-id": "chat-1"}), answer)
    assert "relay_notes" not in first["structuredContent"]
