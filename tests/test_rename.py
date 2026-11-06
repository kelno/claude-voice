"""Renaming a session the bridge started: the new name goes into its transcript, where Claude Code
reads a name another process gives it."""

import json
import os

import pytest
from claude_agent_sdk import project_key_for_directory
from fakes import FakeClaude
from mcp.client import Client

from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

PID = os.getpid()
SID = "5388a106-98e3-4eb0-bd1f-5c1c625c8339"


class World:
    """The live registry, the bridge's record of what it started, and a real transcript file."""

    def __init__(self, tmp_path):
        self.root = tmp_path / "home"
        (self.root / "billing").mkdir(parents=True)
        self.cwd = str(self.root / "billing")
        self.live = tmp_path / "live"
        self.live.mkdir()
        self.projects = tmp_path / "projects"
        self.transcript = self.projects / project_key_for_directory(self.cwd) / f"{SID}.jsonl"
        self.transcript.parent.mkdir(parents=True)
        self.transcript.write_text(
            json.dumps({"type": "custom-title", "customTitle": "Old name", "sessionId": SID}) + "\n", encoding="utf-8"
        )
        self.db = tmp_path / "b.db"

    def running(self, name="Old name", status="idle"):
        entry = {"pid": PID, "sessionId": SID, "cwd": self.cwd, "name": name, "kind": "bg", "status": status}
        (self.live / f"{PID}.json").write_text(json.dumps(entry), encoding="utf-8")

    def started(self, name="Old name", status="idle"):
        self.running(name, status)
        Store(self.db).record_started(SID, name, self.cwd)

    def lines(self):
        return [json.loads(line) for line in self.transcript.read_text(encoding="utf-8").splitlines()]

    def bridge(self):
        m = SessionManager(Store(self.db), project_root=self.root, client_factory=FakeClaude())
        return build_server(m, conversations=lambda **k: [], live_dir=self.live, projects_dir=self.projects)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


async def rename(world, session, name):
    async with Client(world.bridge()) as c:
        res = await c.call_tool("rename_session", {"session": session, "name": name})
    return res


async def test_a_session_the_bridge_started_gets_its_new_name_in_its_transcript(world):
    world.started()
    res = await rename(world, "Old name", "Switchboard: bridge fixes")
    assert not res.is_error, res.content
    assert res.structured_content["status"] == "renamed"
    assert world.lines()[-2:] == [
        {"type": "custom-title", "customTitle": "Switchboard: bridge fixes", "sessionId": SID},
        {"type": "agent-name", "agentName": "Switchboard: bridge fixes", "sessionId": SID},
    ]
    assert Store(world.db).started_session("Switchboard: bridge fixes")["claude_session_id"] == SID


async def test_only_a_session_the_bridge_started_is_ever_renamed(world):
    world.running()
    res = await rename(world, "Old name", "Mine now")
    assert not res.is_error, res.content
    assert res.structured_content["status"] == "not_started_here"
    assert len(world.lines()) == 1


async def test_a_session_mid_turn_is_not_renamed(world):
    world.started(status="busy")
    res = await rename(world, SID, "Later")
    assert res.structured_content["status"] == "busy"
    assert len(world.lines()) == 1


async def test_a_new_name_is_cleaned_and_kept_short(world):
    world.started()
    res = await rename(world, "Old name", "  Two\nlines\t" + "x" * 100)
    name = res.structured_content["name"]
    assert name.startswith("Two lines x") and len(name) == 60
    blank = await rename(world, "Old name", " \n ")
    assert blank.is_error and len(world.lines()) == 3
