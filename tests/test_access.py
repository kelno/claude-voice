"""Folders outside the root are listed, and opened one at a time by the owner (ADR-CV-006)."""

import json

import httpx
import pytest
from fakes import FakeClaude
from mcp.client import Client
from softkey import SoftKey

from claude_voice import access, passkeys, procs
from claude_voice.server import build_app, build_server
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
        exempt=access.default_exempt(world["home"]),
    )
    config = world["tmp"] / ".claude.json"  # Claude Code's own file, never the real one here
    return m, build_server(m, conversations=lambda **k: [], live_dir=world["live"], claude_config=config)


def trusted(world) -> dict:
    """Claude Code's workspace trust per folder, as its config file holds it."""
    config = json.loads((world["tmp"] / ".claude.json").read_text(encoding="utf-8"))
    return {k: v.get("hasTrustDialogAccepted") for k, v in config.get("projects", {}).items()}


async def call(client, name, **args):
    res = await client.call_tool(name, args)
    assert not res.is_error, res.content
    return res.structured_content


async def owner_grants(c, srv, world, **args):
    """request_access, then the owner's passkey on the bridge's page: what the bridge did."""
    asked = await call(c, "request_access", **args)
    assert asked["status"] == "waiting_for_owner"
    book = passkeys.for_url(Store(world["tmp"] / "bridge.db"), "http://localhost")
    key = SoftKey("localhost", "http://localhost")
    token = book.new_registration()
    book.register(token, key.register(book.registration_options(token)), "test")
    page = asked["link"].removeprefix("http://localhost")
    transport = httpx.ASGITransport(app=build_app(srv), client=("127.0.0.1", 4321))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as h:
        options = (await h.post(page + "/challenge")).json()
        done = await h.post(page + "/confirm", json=key.assert_(options))
    assert done.status_code == 200, done.text
    return done.json()["folder"]


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
    assert notes["access"] == "folder_not_open"
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
    m, srv = server(world)
    async with Client(srv) as c:
        granted = await owner_grants(c, srv, world, session="Notes")
        assert granted["folder"] == str(world["elsewhere"].resolve())
        listed = {s["name"]: s for s in (await call(c, "list_active_sessions"))["sessions"]}
        assert "access" not in listed["Notes"] and "access" not in listed["Notes, deeper"]
        assert m.resolve_project(str(world["elsewhere"] / "sub")) == (world["elsewhere"] / "sub").resolve()

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
        assert listed["Home"]["access"] == "folder_closed" and listed["Keys"]["access"] == "folder_closed"
        holds = await refused(c, "request_access", session="Home")
        inside = await refused(c, "request_access", session="Keys")
        unreadable = await refused(c, "read_session_output", session="Keys")
    ssh = str((world["home"] / ".ssh").resolve())
    # The refusal names the closed folder and how the session's folder relates to it.
    assert "holds" in holds and ssh in holds
    assert "inside" in inside and ssh in inside
    assert "inside" in unreadable and ssh in unreadable


async def test_a_denied_folder_stays_closed_inside_the_root(world):
    running(world, 5, "Keys", world["home"] / ".ssh" / "keys")
    m, srv = server(world, root=world["home"])
    async with Client(srv) as c:
        listed = {s["name"]: s for s in (await call(c, "list_active_sessions"))["sessions"]}
        assert listed["Keys"]["access"] == "folder_closed"
    with pytest.raises(ValueError):
        m.resolve_project(".ssh")


async def test_a_folder_no_session_runs_in_is_opened_by_its_path(world):
    # To start work remotely in a folder never used from the bridge, the owner opens it by path.
    m, srv = server(world)
    async with Client(srv) as c:
        granted = await owner_grants(c, srv, world, folder=str(world["elsewhere"]))
    assert granted["folder"] == str(world["elsewhere"].resolve())
    assert m.resolve_project(str(world["elsewhere"] / "sub")) == (world["elsewhere"] / "sub").resolve()


async def test_a_folder_opened_by_path_is_trusted_by_claude_code_too(world):
    # Claude Code won't start a background session in a folder it was never trusted in, so the
    # owner's passkey confirmation of request_access covers its trust too, as its docs say to set it.
    (world["tmp"] / ".claude.json").write_text(
        json.dumps({"numStartups": 7, "projects": {"C:/elsewhere": {"hasTrustDialogAccepted": True, "x": 1}}}),
        encoding="utf-8",
    )
    _, srv = server(world)
    async with Client(srv) as c:
        granted = await owner_grants(c, srv, world, folder=str(world["elsewhere"]))
    key = world["elsewhere"].resolve().as_posix()
    assert granted["trusted"] == key
    assert trusted(world) == {"C:/elsewhere": True, key: True}
    config = json.loads((world["tmp"] / ".claude.json").read_text(encoding="utf-8"))
    assert config["numStartups"] == 7 and config["projects"]["C:/elsewhere"]["x"] == 1


async def test_a_folder_inside_a_repository_is_trusted_at_the_repository_root(world):
    # Claude Code keys trust on the repository's root, so that is the folder it checks.
    repo = world["elsewhere"]
    (repo / ".git").mkdir()
    (world["tmp"] / ".claude.json").write_text("{}", encoding="utf-8")
    _, srv = server(world)
    async with Client(srv) as c:
        granted = await owner_grants(c, srv, world, folder=str(repo / "sub"))
    assert granted["trusted"] == repo.resolve().as_posix()
    assert trusted(world) == {repo.resolve().as_posix(): True}


async def test_a_trust_that_cannot_be_written_leaves_the_grant_and_says_why(world):
    (world["tmp"] / ".claude.json").write_text("not json", encoding="utf-8")
    m, srv = server(world)
    async with Client(srv) as c:
        granted = await owner_grants(c, srv, world, folder=str(world["elsewhere"]))
    assert granted["granted"] and "trusted" not in granted
    assert "trust" in granted["trust_not_set"]
    assert (world["tmp"] / ".claude.json").read_text(encoding="utf-8") == "not json"
    assert m.resolve_project(str(world["elsewhere"])) == world["elsewhere"].resolve()


async def test_a_session_s_folder_is_left_to_claude_code_s_own_trust(world):
    # A session already runs there, so Claude Code trusts it; its config is left alone.
    running(world, 2, "Notes", world["elsewhere"])
    _, srv = server(world)
    async with Client(srv) as c:
        granted = await owner_grants(c, srv, world, session="Notes")
    assert "trusted" not in granted
    assert not (world["tmp"] / ".claude.json").exists()


async def test_a_folder_by_path_is_refused_when_closed_relative_missing_or_doubled(world):
    running(world, 2, "Notes", world["elsewhere"])
    _, srv = server(world)
    async with Client(srv) as c:
        closed = await refused(c, "request_access", folder=str(world["home"]))
        relative = await refused(c, "request_access", folder="notes")
        missing = await refused(c, "request_access", folder=str(world["tmp"] / "nowhere"))
        both = await refused(c, "request_access", session="Notes", folder=str(world["elsewhere"]))
        neither = await refused(c, "request_access")
    assert "holds" in closed
    assert "full path" in relative
    assert "not a directory" in missing
    assert "one of" in both and "one of" in neither


async def test_revoking_a_grant_closes_the_folder_again(world):
    running(world, 2, "Notes", world["elsewhere"])
    _, srv = server(world)
    async with Client(srv) as c:
        await owner_grants(c, srv, world, session="Notes")
        await call(c, "revoke_access", folder=str(world["elsewhere"]))
        listed = {s["name"]: s for s in (await call(c, "list_active_sessions"))["sessions"]}
        assert listed["Notes"]["access"] == "folder_not_open"
        await refused(c, "revoke_access", folder=str(world["elsewhere"]))


def scratch_world(world):
    """The desktop app's scratch folders, under a closed AppData, as on Windows."""
    workspaces = world["home"] / "AppData" / "Local" / "Claude-Data" / "pkg" / "scratch-workspaces"
    scratch = workspaces / "acct" / "org" / "scratch-2026-10-29-f06e00"
    (scratch / "notes").mkdir(parents=True)
    (world["home"] / "AppData" / "Local" / "Claude-Data" / "pkg" / "Local Storage").mkdir()
    return workspaces, scratch


async def test_only_a_desktop_scratch_folder_is_opened_inside_a_denied_folder(world):
    workspaces, scratch = scratch_world(world)
    running(world, 7, "Scratch", scratch)
    running(world, 8, "Scratch, deeper", scratch / "notes")
    running(world, 9, "Workspaces", workspaces)
    running(world, 10, "App storage", world["home"] / "AppData" / "Local" / "Claude-Data" / "pkg" / "Local Storage")
    _, srv = server(world)
    async with Client(srv) as c:
        listed = {s["name"]: s for s in (await call(c, "list_active_sessions"))["sessions"]}
        assert listed["Scratch"]["access"] == "folder_not_open"
        assert listed["Workspaces"]["access"] == "folder_closed"
        assert listed["App storage"]["access"] == "folder_closed"
        granted = await owner_grants(c, srv, world, session="Scratch")
        assert granted["folder"] == str(scratch.resolve())
        listed = {s["name"]: s for s in (await call(c, "list_active_sessions"))["sessions"]}
        assert "access" not in listed["Scratch"] and "access" not in listed["Scratch, deeper"]
        await refused(c, "request_access", session="Workspaces")


async def test_the_bridges_own_messengers_are_not_listed(world):
    courier = world["tmp"] / "claude-voice-msg-ab12"
    courier.mkdir()
    running(world, 6, "Owner via claude-voice", courier)
    _, srv = server(world)
    async with Client(srv) as c:
        listed = (await call(c, "list_active_sessions"))["sessions"]
    assert listed == []
