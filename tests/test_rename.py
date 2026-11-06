"""Renaming a session the bridge started: the new name goes into its job record, which a running
background session watches and takes its name from at once, and which waking it again reads."""

import json
import os

import pytest
from fakes import FakeClaude
from mcp.client import Client

from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

PID = os.getpid()
SID = "89bb4528-3840-4006-864a-5aa9930e26e0"
FLAGS = ["--name", "Old name", "--remote-control", "--effort", "low"]


class World:
    """The live registry, the bridge's record of what it started, and Claude Code's job record."""

    def __init__(self, tmp_path):
        self.root = tmp_path / "home"
        (self.root / "billing").mkdir(parents=True)
        self.cwd = str(self.root / "billing")
        self.live = tmp_path / "live"
        self.live.mkdir()
        self.jobs = tmp_path / "jobs"
        self.job = self.jobs / SID[:8] / "state.json"
        self.job.parent.mkdir(parents=True)
        record = {"state": "idle", "name": "Old name", "nameSource": "user", "respawnFlags": FLAGS, "sessionId": SID}
        self.job.write_text(json.dumps(record, indent=2), encoding="utf-8")
        self.db = tmp_path / "b.db"

    def running(self, name="Old name", status="idle"):
        entry = {"pid": PID, "sessionId": SID, "cwd": self.cwd, "name": name, "kind": "bg", "status": status}
        (self.live / f"{PID}.json").write_text(json.dumps(entry), encoding="utf-8")

    def started(self, name="Old name", status="idle"):
        self.running(name, status)
        Store(self.db).record_started(SID, name, self.cwd)

    def record(self):
        return json.loads(self.job.read_text(encoding="utf-8"))

    def bridge(self):
        m = SessionManager(Store(self.db), project_root=self.root, client_factory=FakeClaude())
        return build_server(m, conversations=lambda **k: [], live_dir=self.live, jobs_dir=self.jobs)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


async def rename(world, session, name):
    async with Client(world.bridge()) as c:
        return await c.call_tool("rename_session", {"session": session, "name": name})


async def test_a_session_the_bridge_started_gets_its_new_name_in_its_job_record(world):
    world.started()
    res = await rename(world, "Old name", "Switchboard: bridge fixes")
    assert not res.is_error, res.content
    assert res.structured_content["status"] == "renamed"
    record = world.record()
    assert (record["name"], record["nameSource"]) == ("Switchboard: bridge fixes", "user")
    # Waking the session again starts it with these flags, so the name lasts past a stop.
    assert record["respawnFlags"] == ["--name", "Switchboard: bridge fixes", "--remote-control", "--effort", "low"]
    assert record["state"] == "idle" and record["sessionId"] == SID
    assert Store(world.db).started_session("Switchboard: bridge fixes")["claude_session_id"] == SID


async def test_only_a_session_the_bridge_started_is_ever_renamed(world):
    world.running()
    res = await rename(world, "Old name", "Mine now")
    assert not res.is_error, res.content
    assert res.structured_content["status"] == "not_started_here"
    assert world.record()["name"] == "Old name"


async def test_a_session_mid_turn_is_not_renamed(world):
    # Claude Code rewrites the job record often during a turn; a change then could be lost.
    world.started(status="busy")
    res = await rename(world, SID, "Later")
    assert res.structured_content["status"] == "busy"
    assert world.record()["name"] == "Old name"


async def test_a_new_name_is_cleaned_and_kept_short(world):
    world.started()
    res = await rename(world, "Old name", "  Two\nlines\t" + "x" * 100)
    name = res.structured_content["name"]
    assert name.startswith("Two lines x") and len(name) == 60
    blank = await rename(world, name, " \n ")
    assert blank.is_error and world.record()["name"] == name


async def test_a_session_without_a_job_record_is_reported_not_renamed(world):
    world.started()
    world.job.unlink()
    res = await rename(world, "Old name", "New")
    assert res.is_error and "job record" in res.content[0].text
