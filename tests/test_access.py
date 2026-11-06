"""Folders outside the root are listed, and opened one at a time by the owner (ADR-CV-006)."""

import json

import pytest
from fakes import FakeClaude
from mcp.client import Client

from claude_voice import access, procs
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(procs, "alive", lambda pid: True)
    home = tmp_path / "home"
    root = tmp_path / "work"
    elsewhere = tmp_path / "notes"
    for d in (home / ".ssh" / "keys", root / "app", elsewhere / "sub"):
        d.mkdir(parents=True)
    live = tmp_path / "live"
    live.mkdir()
    return {"tmp": tmp_path, "home": home, "root": root, "elsewhere": elsewhere, "live": live}


def running(world, pid, name, cwd, status="idle"):
    entry = {
        "pid": pid,
        "sessionId": f"sid-{pid}",
        "cwd": str(cwd),
        "name": name,
        "status": status,
        "kind": "interactive",
    }
    (world["live"] / f"{pid}.json").write_text(json.dumps(entry), encoding="utf-8")


def server(world, root=None):
    m = SessionManager(
        Store(world["tmp"] / "bridge.db"),
        project_root=root or world["root"],
        client_factory=FakeClaude(),
        denied=access.default_denied(world["home"]),
    )
    return m, build_server(m, conversations=lambda **k: [], live_dir=world["live"])


async def call(client, name, **args):
    res = await client.call_tool(name, args)
    assert not res.is_error, res.content
    return res.structured_content


async def refused(client, name, **args):
    res = await client.call_tool(name, args)
    assert res.is_error
    return res.content[0].text


async def test_a_session_outside_the_open_folders_is_listed_as_needing_approval(world):
    running(world, 1, "app-fix", world["root"] / "app")
    running(world, 2, "Notes", world["elsewhere"])
    _, srv = server(world)
    async with Client(srv) as c:
        listed = {s["name"]: s for s in (await call(c, "list_active_sessions"))["sessions"]}
    assert "access" not in listed["app-fix"]
    notes = listed["Notes"]
    assert notes["access"] == "needs_approval"
    assert notes["folder"] == str(world["elsewhere"].resolve())
    assert set(notes) == {"claude_session_id", "name", "folder", "status", "kind", "access", "minutes_since_update"}


async def test_a_session_needing_approval_cannot_be_read_or_messaged(world):
    running(world, 2, "Notes", world["elsewhere"])
    _, srv = server(world)
    async with Client(srv) as c:
        assert "request_access" in await refused(c, "read_session_output", session="Notes")
        assert "request_access" in await refused(c, "message_active_session", session="Notes", message="hi")


async def test_granting_opens_a_sessions_folder_and_everything_under_it_for_good(world):
    running(world, 2, "Notes", world["elsewhere"])
    running(world, 3, "Notes, deeper", world["elsewhere"] / "sub")
    _, srv = server(world)
    async with Client(srv) as c:
        granted = await call(c, "request_access", session="Notes")
        assert granted["folder"] == str(world["elsewhere"].resolve())
        listed = {s["name"]: s for s in (await call(c, "list_active_sessions"))["sessions"]}
        assert "access" not in listed["Notes"] and "access" not in listed["Notes, deeper"]
        started = await call(c, "create_session", project=str(world["elsewhere"] / "sub"))
        assert started["project_path"] == str((world["elsewhere"] / "sub").resolve())

    _, again = server(world)  # a restarted bridge keeps the grant
    async with Client(again) as c:
        listed = {s["name"]: s for s in (await call(c, "list_active_sessions"))["sessions"]}
        health = await call(c, "health")
    assert "access" not in listed["Notes"]
    assert health["access"]["granted"] == [str(world["elsewhere"].resolve())]


async def test_a_folder_holding_or_inside_a_denied_folder_is_never_granted(world):
    running(world, 4, "Home", world["home"])
    running(world, 5, "Keys", world["home"] / ".ssh" / "keys")
    _, srv = server(world)
    async with Client(srv) as c:
        listed = {s["name"]: s for s in (await call(c, "list_active_sessions"))["sessions"]}
        assert listed["Home"]["access"] == "blocked" and listed["Keys"]["access"] == "blocked"
        assert "never" in await refused(c, "request_access", session="Home")
        assert "never" in await refused(c, "request_access", session="Keys")


async def test_a_denied_folder_stays_closed_inside_the_root(world):
    running(world, 5, "Keys", world["home"] / ".ssh" / "keys")
    _, srv = server(world, root=world["home"])
    async with Client(srv) as c:
        listed = {s["name"]: s for s in (await call(c, "list_active_sessions"))["sessions"]}
        assert listed["Keys"]["access"] == "blocked"
        await refused(c, "create_session", project=".ssh")


async def test_revoking_a_grant_closes_the_folder_again(world):
    running(world, 2, "Notes", world["elsewhere"])
    _, srv = server(world)
    async with Client(srv) as c:
        await call(c, "request_access", session="Notes")
        await call(c, "revoke_access", folder=str(world["elsewhere"]))
        listed = {s["name"]: s for s in (await call(c, "list_active_sessions"))["sessions"]}
        assert listed["Notes"]["access"] == "needs_approval"
        await refused(c, "revoke_access", folder=str(world["elsewhere"]))


async def test_the_bridges_own_messengers_are_not_listed(world):
    courier = world["tmp"] / "claude-voice-msg-ab12"
    courier.mkdir()
    running(world, 6, "Owner via claude-voice", courier)
    _, srv = server(world)
    async with Client(srv) as c:
        listed = (await call(c, "list_active_sessions"))["sessions"]
    assert listed == []
