"""Handing a session the bridge started to the desktop app, so it shows in the sidebar."""

import base64
import json
import os

import pytest
from fakes import FakeClaude
from mcp.client import Client
from test_live import msg

from claude_voice import transcripts
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

PID = os.getpid()
SID = "1f2e6117-786c-46aa-b15c-21922dc1beea"


class World:
    """The live registry, a transcript per session, and fakes for stopping and opening."""

    def __init__(self, tmp_path):
        self.root = tmp_path / "home"
        (self.root / "billing").mkdir(parents=True)
        self.live = tmp_path / "live"
        self.live.mkdir()
        self.db = tmp_path / "b.db"
        self.transcripts = {}
        self.stopped = []
        self.opened = []

    def running(self, name, sid=SID, status="idle", spoke=True):
        cwd = str(self.root / "billing")
        (self.live / f"{name}.json").write_text(
            json.dumps({"pid": PID, "sessionId": sid, "cwd": cwd, "name": name, "kind": "bg", "status": status})
        )
        said = [msg("user", "hi"), msg("assistant", [{"type": "text", "text": "ready"}])]
        self.transcripts[sid] = said if spoke else []
        return cwd

    def started(self, name, sid=SID, **kw):
        cwd = self.running(name, sid, **kw)
        Store(self.db).record_started(sid, name, cwd)
        return cwd

    async def stop(self, session_id):
        self.stopped.append(session_id)
        for f in self.live.glob("*.json"):
            if json.loads(f.read_text(encoding="utf-8")).get("sessionId") == session_id:
                f.unlink()

    async def open(self, session_id, cwd, title):
        self.opened.append((session_id, cwd, title))

    def bridge(self):
        m = SessionManager(Store(self.db), project_root=self.root, client_factory=FakeClaude())
        return build_server(
            m,
            conversations=lambda **k: [],
            live_dir=self.live,
            stop_background=self.stop,
            open_in_desktop=self.open,
            read_transcript=lambda sid, directory: list(self.transcripts.get(sid, [])),
            start_seconds=1.0,
        )


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


async def hand(world, name):
    async with Client(world.bridge()) as c:
        res = await c.call_tool("hand_to_desktop", {"name": name})
    assert not res.is_error, res.content
    return res.structured_content


async def test_a_started_idle_session_is_stopped_then_opened_in_the_desktop_app(world):
    cwd = world.started("invoices")
    out = await hand(world, "invoices")
    assert out["status"] == "handed_over"
    assert out["claude_session_id"] == SID
    assert world.stopped == [SID]  # the desktop app only takes a stopped session
    assert world.opened == [(SID, cwd, "invoices")]
    assert not list(world.live.glob("*.json"))


async def test_a_session_already_stopped_is_opened_without_stopping_it_again(world):
    cwd = world.started("invoices")
    await world.stop(SID)
    world.stopped.clear()
    out = await hand(world, "invoices")
    assert out["status"] == "handed_over"
    assert world.stopped == []
    assert world.opened == [(SID, cwd, "invoices")]


async def test_only_a_session_the_bridge_started_is_handed_over(world):
    world.running("my-terminal")
    out = await hand(world, "my-terminal")
    assert out["status"] == "not_started_here"
    assert world.stopped == [] and world.opened == []


async def test_a_session_in_the_middle_of_a_turn_is_left_alone(world):
    world.started("invoices", status="busy")
    out = await hand(world, "invoices")
    assert out["status"] == "busy"
    assert world.stopped == [] and world.opened == []


async def test_a_session_that_never_spoke_is_not_handed_over(world):
    # Claude Code writes no conversation before the first turn, so there is nothing to open.
    world.started("invoices", spoke=False)
    out = await hand(world, "invoices")
    assert out["status"] == "no_conversation"
    assert world.stopped == [] and world.opened == []


async def test_an_unknown_session_is_reported(world):
    out = await hand(world, "nobody")
    assert out["status"] == "not_started_here"


def decoded_script(command):
    return base64.b64decode(command[command.index("-EncodedCommand") + 1]).decode("utf-16-le")


def test_the_handover_runs_in_a_named_terminal_tab_the_owner_can_see():
    command = transcripts.desktop_handover_command(SID, r"D:\work\voice bridge", "Phone handover; test")
    assert command[0].lower().endswith(("wt", "wt.exe"))
    assert command[command.index("--title") + 1] == "Phone handover test"  # one argument; no ";" for wt to split on
    assert command[command.index("-d") + 1] == r"D:\work\voice bridge"
    script = decoded_script(command)
    assert f"claude --desktop --resume '{SID}'" in script
    assert "Read-Host" in script  # a failed handover stays on screen


def test_only_a_session_id_is_ever_put_into_the_handover_script():
    with pytest.raises(ValueError):
        transcripts.desktop_handover_command("x'; Remove-Item", r"D:\work", "x")


def test_sessions_the_bridge_starts_may_edit_their_project_directly():
    # Claude Code keeps a background session's edits in a worktree unless told otherwise;
    # the owner wants that off for the bridge's sessions only, not every background session.
    command = transcripts.background_command("claude", "deep")
    settings = json.loads(command[command.index("--settings") + 1])
    assert settings == {"worktree": {"bgIsolation": "none"}}
    hooked = transcripts.background_command("claude", "deep", settings=json.dumps({"hooks": {"x": []}}))
    assert json.loads(hooked[hooked.index("--settings") + 1]) == {
        "worktree": {"bgIsolation": "none"},
        "hooks": {"x": []},
    }
