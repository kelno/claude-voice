"""Each session's link that opens it in the Claude app, given wherever a session is named to a relay.

Agents kept building the link by hand from Claude Code's job records, and new sessions had to be taught
how (owner, 2026-11-19: "if sessions need to be explained, that's an issue in general"). So the bridge
gives it: list_active_sessions, start_active_session and attach_conversation carry app_url for each
session on Remote Control, and nothing for one that isn't.
"""

import asyncio
import json
import time
from datetime import timedelta

import pytest
from fakes import FakeClaude
from mcp.client import Client
from test_offline import job, stamp
from test_start import OLD, PID, Starter, bridge, resuming

from claude_voice import app_links
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

REMOTE = "session_017aWUqgaoE7NFCEoEjYFvfc"
LINK = f"https://claude.ai/code/{REMOTE}"


@pytest.fixture
def world(tmp_path):
    """The root with its billing folder, and Claude Code's folder of live entries, as test_start has them."""
    root = tmp_path / "home"
    (root / "billing").mkdir(parents=True)
    (tmp_path / "live").mkdir()
    return root, tmp_path / "live"


def live_entry(live, name, sid, cwd, **more):
    entry = {"pid": PID, "sessionId": sid, "cwd": str(cwd), "name": name, "kind": "bg", "status": "idle"}
    (live / f"{name}.json").write_text(json.dumps({**entry, **more}))


async def test_a_listed_session_on_remote_control_carries_its_link_and_one_without_none(tmp_path, world):
    root, live = world
    live_entry(live, "linked", "s1", root / "billing", bridgeSessionId=REMOTE)
    live_entry(live, "local", "s2", root / "billing")
    async with Client(bridge(tmp_path, world, Starter(live))) as c:
        rows = {r["name"]: r for r in (await c.call_tool("list_active_sessions", {})).structured_content["sessions"]}
    assert rows["linked"]["app_url"] == LINK
    assert "app_url" not in rows["local"]


async def test_a_session_outside_the_open_folders_still_carries_its_link(tmp_path, world):
    # Only its name, folder and status are read, but the link opens it for the owner, not for the relay.
    _, live = world
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    live_entry(live, "outside", "s3", elsewhere, bridgeSessionId=REMOTE)
    async with Client(bridge(tmp_path, world, Starter(live))) as c:
        rows = (await c.call_tool("list_active_sessions", {})).structured_content["sessions"]
    assert rows[0]["access"] == "folder_not_open" and rows[0]["app_url"] == LINK


async def test_a_session_the_app_shows_offline_carries_its_link_from_its_job_record(tmp_path):
    # The job record keeps the Remote Control id as cse_…; the app's link takes it as session_….
    root = tmp_path / "home"
    root.mkdir()
    (tmp_path / "live").mkdir()
    sid = "e33956a5-eb04-4f6a-a693-3e7cdf3be64f"
    job(tmp_path / "jobs", sid, "Bridge code review", str(root))
    log = tmp_path / "daemon.log"
    log.write_text(f"[{stamp(timedelta(hours=2))}] [bg] bg retire {sid[:8]}: settled, idle 8h\n", encoding="utf-8")
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    server = build_server(m, live_dir=tmp_path / "live", daemon_log=log, jobs_dir=tmp_path / "jobs")
    async with Client(server) as c:
        out = (await c.call_tool("list_active_sessions", {})).structured_content
    assert out["offline_in_app"][0]["app_url"] == LINK


async def test_a_started_session_comes_back_with_its_link(tmp_path, world):
    starter = Starter(world[1], entry={"bridgeSessionId": REMOTE})
    async with Client(bridge(tmp_path, world, starter)) as c:
        args = {"folder": "billing", "name": "invoices"}
        out = (await c.call_tool("start_active_session", args)).structured_content
        again = (await c.call_tool("start_active_session", args)).structured_content
    assert out["app_url"] == LINK
    assert again["already_running"] is True and again["app_url"] == LINK


class LateLink(Starter):
    """Lists the session first and gives it its Remote Control id a moment later, as Claude Code
    did when measured on 2026-11-19 (1.3 s later)."""

    def registered(self, cwd, name, sid):
        note = super().registered(cwd, name, sid)
        path = self.live / f"{name}.json"
        entry = json.loads(path.read_text())

        async def later():
            await asyncio.sleep(0.3)
            path.write_text(json.dumps({**entry, "bridgeSessionId": REMOTE}))

        self.pending = asyncio.ensure_future(later())
        return note


async def test_a_started_session_waits_a_moment_for_remote_control_to_give_its_link(tmp_path, world, monkeypatch):
    monkeypatch.setattr(app_links, "WAIT_SECONDS", 2.0)
    async with Client(bridge(tmp_path, world, LateLink(world[1]))) as c:
        out = (await c.call_tool("start_active_session", {"folder": "billing", "name": "late"})).structured_content
    assert out["app_url"] == LINK


async def test_a_session_started_without_remote_control_comes_back_at_once_without_a_link(tmp_path, world, monkeypatch):
    monkeypatch.setattr(app_links, "WAIT_SECONDS", 2.0)
    async with Client(bridge(tmp_path, world, Starter(world[1]))) as c:
        began = time.monotonic()
        out = (
            await c.call_tool("start_active_session", {"folder": "billing", "name": "plain", "remote_control": False})
        ).structured_content
    assert "app_url" not in out and time.monotonic() - began < 1.0


async def test_a_link_that_never_comes_leaves_the_start_without_one_after_the_wait(tmp_path, world, monkeypatch):
    monkeypatch.setattr(app_links, "WAIT_SECONDS", 0.3)
    async with Client(bridge(tmp_path, world, Starter(world[1]))) as c:
        out = (await c.call_tool("start_active_session", {"folder": "billing", "name": "slow"})).structured_content
    assert out["name"] == "{slow}" and "app_url" not in out


async def test_a_resumed_conversation_comes_back_with_its_link(tmp_path, world):
    async def known():
        return {OLD}

    starter = Starter(world[1], entry={"bridgeSessionId": REMOTE})
    async with Client(resuming(tmp_path, world, starter, background_sessions=known)) as c:
        out = (await c.call_tool("attach_conversation", {"claude_session_id": OLD})).structured_content
    assert out["claude_session_id"] == OLD and out["app_url"] == LINK


async def test_a_conversation_still_running_comes_back_with_its_link(tmp_path, world):
    root, live = world
    live_entry(live, "Old one", OLD, root / "billing", bridgeSessionId=REMOTE)
    async with Client(resuming(tmp_path, world, Starter(live))) as c:
        out = (await c.call_tool("attach_conversation", {"claude_session_id": OLD})).structured_content
    assert out["already_running"] is True and out["app_url"] == LINK
