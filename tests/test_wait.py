"""Waiting for a session the bridge started, instead of asking for its recap again and again."""

import asyncio

import pytest
from claude_agent_sdk import ToolPermissionContext
from fakes import Call, FakeClaude, Pause, init, result
from mcp.client import Client

from claude_voice.approvals import ApprovalBroker
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


@pytest.fixture
def root(tmp_path):
    (tmp_path / "src" / "app").mkdir(parents=True)
    return tmp_path / "src"


def make(tmp_path, root, *scripts):
    store = Store(tmp_path / "bridge.db")
    m = SessionManager(store, project_root=root, client_factory=FakeClaude(*scripts), approvals=ApprovalBroker(store))
    return m, build_server(m, poll_seconds=0.01)


async def call(client, name, **args):
    res = await client.call_tool(name, args)
    assert not res.is_error, res.content
    return res.structured_content


async def test_waiting_returns_once_the_task_is_finished(tmp_path, root):
    pause = Pause()
    m, server = make(tmp_path, root, [init("c-1"), pause, result("All done.", "c-1")])
    async with Client(server) as client:
        s = await call(client, "create_session", project="app")
        await call(client, "send_task", session_id=s["id"], prompt="go")
        await pause.reached.wait()
        waiting = asyncio.create_task(call(client, "wait_for_session", session_id=s["id"], wait_seconds=10))
        await asyncio.sleep(0.05)
        assert not waiting.done()

        pause.release.set()
        out = await waiting
    assert out["waited"] == "finished"
    assert out["status"] == "idle"
    assert out["last_result"] == "All done."


async def test_waiting_returns_as_soon_as_an_approval_is_needed(tmp_path, root):
    async def ask(options):
        await options.can_use_tool("Bash", {"command": "make"}, ToolPermissionContext())

    m, server = make(tmp_path, root, [init("c-1"), Call(ask), result("built", "c-1")])
    async with Client(server) as client:
        s = await call(client, "create_session", project="app")
        await call(client, "send_task", session_id=s["id"], prompt="build")
        out = await call(client, "wait_for_session", session_id=s["id"], wait_seconds=10)
        assert out["waited"] == "needs_approval"
        assert [a["tool"] for a in out["pending_approvals"]] == ["Bash"]
        await call(client, "cancel", session_id=s["id"])


async def test_waiting_gives_up_after_the_wait_and_says_the_task_still_runs(tmp_path, root):
    pause = Pause()
    m, server = make(tmp_path, root, [init("c-1"), pause, result("late", "c-1")])
    async with Client(server) as client:
        s = await call(client, "create_session", project="app")
        await call(client, "send_task", session_id=s["id"], prompt="go")
        await pause.reached.wait()
        out = await call(client, "wait_for_session", session_id=s["id"], wait_seconds=0.1)
        assert out["waited"] == "still_running"
        assert out["status"] == "running"
        await call(client, "cancel", session_id=s["id"])


async def test_waiting_on_a_session_with_no_task_returns_at_once(tmp_path, root):
    m, server = make(tmp_path, root)
    async with Client(server) as client:
        s = await call(client, "create_session", project="app")
        out = await call(client, "wait_for_session", session_id=s["id"], wait_seconds=10)
    assert out["waited"] == "not_running"
    assert out["status"] == "idle"
