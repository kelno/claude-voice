"""The guide is handed over once per conversation, and again after a Claude Code compaction.

A Claude app conversation is known by its MCP session. A Claude Code session sends none, so the
bridge knows it by the transcript that holds the call, and sees a compaction there: compaction can
drop a guide that came as a tool result, so the next result carries it again.
"""

import json

from claude_voice.server import INSTRUCTIONS, result_notes


class Request:
    def __init__(self, headers):
        self.headers = headers


class Ctx:
    def __init__(self, headers=None):
        self.method = "tools/call"
        self.params = {"name": "list_projects", "arguments": {}}
        self.request = Request(headers or {})


async def answer(ctx):
    return {"structuredContent": {"projects": []}, "content": []}


def line(entry):
    return json.dumps(entry) + "\n"


async def test_a_claude_code_session_gets_the_guide_on_its_first_call_and_again_after_a_compaction(tmp_path):
    transcript = tmp_path / "desk-1.jsonl"
    transcript.write_text(line({"type": "assistant", "message": {"content": []}}), encoding="utf-8")
    note = result_notes(claude_code_transcript=lambda: transcript)

    first = await note(Ctx(), answer)
    second = await note(Ctx(), answer)
    with transcript.open("a", encoding="utf-8") as f:
        f.write(line({"type": "system", "subtype": "compact_boundary"}))
    after = await note(Ctx(), answer)
    later = await note(Ctx(), answer)

    assert first["structuredContent"]["guide"] == INSTRUCTIONS
    assert "guide" not in second["structuredContent"]
    assert after["structuredContent"]["guide"] == INSTRUCTIONS
    assert "guide" not in later["structuredContent"]


async def test_two_claude_code_sessions_each_get_the_guide(tmp_path):
    one, two = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    for t in (one, two):
        t.write_text(line({"type": "user"}), encoding="utf-8")
    calling = [one]
    note = result_notes(claude_code_transcript=lambda: calling[0])

    a = await note(Ctx(), answer)
    calling[0] = two
    b = await note(Ctx(), answer)

    assert a["structuredContent"]["guide"] == INSTRUCTIONS
    assert b["structuredContent"]["guide"] == INSTRUCTIONS


async def test_a_call_from_neither_kind_of_conversation_gets_no_guide(tmp_path):
    note = result_notes(claude_code_transcript=lambda: None)
    out = await note(Ctx(), answer)
    assert "guide" not in out["structuredContent"]


async def test_a_claude_app_conversation_is_still_known_by_its_mcp_session(tmp_path):
    note = result_notes(claude_code_transcript=lambda: None)
    first = await note(Ctx({"mcp-session-id": "chat-1"}), answer)
    second = await note(Ctx({"mcp-session-id": "chat-1"}), answer)
    assert first["structuredContent"]["guide"] == INSTRUCTIONS
    assert "guide" not in second["structuredContent"]
