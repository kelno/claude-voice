"""The side session: a read-only fork of a running session, asked one question (ask_forked_session)."""

import json
import os

import pytest
from fakes import FakeClaude, Pause, init, result, say, use_tool
from mcp.client import Client

from claude_voice import fork
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "src"
    (root / "billing").mkdir(parents=True)
    live = tmp_path / "live"
    live.mkdir()
    entry = {
        "pid": os.getpid(),
        "sessionId": "desk-1",
        "cwd": str(root / "billing"),
        "name": "billing-ab",
        "status": "busy",
    }
    (live / f"{os.getpid()}.json").write_text(json.dumps(entry), encoding="utf-8")
    return tmp_path, root, live


def bridge(world, claude):
    tmp_path, root, live = world
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=claude)
    return build_server(m, conversations=lambda **k: [], live_dir=live, projects_dir=tmp_path / "projects")


async def decide(tool):
    return await fork.refuse_changes({"tool_name": tool, "tool_input": {}}, None, None)


async def test_the_side_session_never_runs_a_tool_that_can_change_anything():
    for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit", "Bash", "PowerShell", "mcp__x__delete"):
        out = await decide(tool)
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny", tool
    for tool in ("Read", "Grep", "Glob"):
        assert await decide(tool) == {}, tool


async def test_a_question_goes_to_a_fork_of_the_session_and_its_answer_comes_back(world):
    claude = FakeClaude(
        [
            init("fork-1"),
            use_tool("Read", {"file_path": "billing.py"}),
            say("Checked."),
            result("The export is in billing.py.", "fork-1"),
        ]
    )
    async with Client(bridge(world, claude)) as c:
        res = await c.call_tool("ask_forked_session", {"session": "billing-ab", "question": "Where is the export?"})
    assert not res.is_error, res.content
    out = res.structured_content
    assert out["status"] == "answered"
    assert out["answer"] == "The export is in billing.py."
    assert out["tools_used"] == {"Read": 1}
    client = claude.clients[0]
    assert client.options.resume == "desk-1" and client.options.fork_session is True
    assert client.options.cwd == str(world[1] / "billing")
    assert client.options.hooks["PreToolUse"][0].hooks == [fork.refuse_changes]
    [prompt] = client.prompts
    assert fork.SIDE_SESSION_NOTE in prompt and "Where is the export?" in prompt


async def test_a_side_session_that_takes_too_long_says_so(world):
    claude = FakeClaude([init("fork-1"), Pause()])
    async with Client(bridge(world, claude)) as c:
        out = (
            await c.call_tool("ask_forked_session", {"session": "billing-ab", "question": "?", "wait_seconds": 0.1})
        ).structured_content
    assert out["status"] == "timed_out"
