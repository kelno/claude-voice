"""Keeping session names current: whose name it is, shown by an asterisk.

A name with an asterisk anywhere in it is the owner's, in the name Claude Code and the app show,
so the owner sees which names are protected (owner, 2026-11-26). An agent may rename any other
session, but never adds an asterisk; a name with one is renamed only at the owner's request
(owner_chose), so a session only proposes one."""

import json
import os

import pytest
from mcp.client import Client
from test_launch import Starter, bridge
from test_rename import Recorder, call, names, write

from claude_voice import every_session, renames
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

HOUR = 3600.0


@pytest.fixture
def root(tmp_path):
    (tmp_path / "src" / "math-quest").mkdir(parents=True)
    return tmp_path / "src"


@pytest.fixture
def live(tmp_path):
    d = tmp_path / "live"
    d.mkdir()
    return d


def session(live, root, name, source=None, sid="mq-id"):
    """A running session named `name`, whose name Claude Code says came from `source`."""
    write(live, os.getpid(), sid, name, root)
    if source is not None:
        path = live / f"{os.getpid()}.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**record, "nameSource": source}), encoding="utf-8")


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def server(tmp_path, root, live, deliver=None, clock=None):
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, clock=clock or Clock())
    return build_server(
        m,
        conversations=lambda directory=None, limit=None: [],
        live_dir=live,
        deliver=deliver or Recorder(),
        read_transcript=lambda sid, directory: [],
        jobs_dir=tmp_path / "jobs",
    )


async def test_a_name_an_agent_gives_is_kept_as_given(tmp_path, root, live):
    session(live, root, "fix the thing", source="auto")
    async with Client(server(tmp_path, root, live)) as c:
        out = await call(c, "rename_session", session="fix the thing", new_name="billing export")
        assert await names(c) == ["billing export"]
    assert out["name"] == "billing export"


async def test_a_name_without_an_asterisk_may_be_renamed_by_an_agent(tmp_path, root, live):
    # Claude Code records a launch title as chosen by someone ("peer"), as it does a name the owner
    # gave: only the asterisk tells them apart.
    session(live, root, "Billing", source="peer")
    async with Client(server(tmp_path, root, live)) as c:
        out = await call(c, "rename_session", session="Billing", new_name="billing export")
    assert out["name"] == "billing export"


async def test_a_name_the_owner_chose_is_kept_as_given(tmp_path, root, live):
    session(live, root, "fix the thing", source="auto")
    async with Client(server(tmp_path, root, live)) as c:
        out = await call(c, "rename_session", session="fix the thing", new_name="Billing*", owner_chose=True)
    assert out["name"] == "Billing*"


async def test_an_agent_cannot_rename_a_session_the_owner_named(tmp_path, root, live):
    session(live, root, "Billing*", source="user")
    async with Client(server(tmp_path, root, live)) as c:
        res = await c.call_tool("rename_session", {"session": "Billing", "new_name": "billing export"})
        assert await names(c) == ["Billing*"]
    assert res.is_error
    assert "owner chose" in res.content[0].text and "propose_session_name" in res.content[0].text


async def test_an_asterisk_anywhere_makes_a_name_the_owner_s(tmp_path, root, live):
    session(live, root, "Bill*ing", source="user")
    async with Client(server(tmp_path, root, live)) as c:
        res = await c.call_tool("rename_session", {"session": "Bill*ing", "new_name": "billing export"})
    assert res.is_error and "owner chose" in res.content[0].text


async def test_only_the_owner_gives_a_session_name_an_asterisk(tmp_path, root, live):
    session(live, root, "fix the thing", source="auto")
    async with Client(server(tmp_path, root, live)) as c:
        res = await c.call_tool("rename_session", {"session": "fix the thing", "new_name": "Billing*"})
        assert await names(c) == ["fix the thing"]
    assert res.is_error and "asterisk" in res.content[0].text and "owner_chose" in res.content[0].text


async def test_an_agent_may_add_and_drop_a_status_after_a_name_the_owner_chose(tmp_path, root, live):
    session(live, root, "Billing*", source="user")
    async with Client(server(tmp_path, root, live)) as c:
        added = await call(c, "rename_session", session="Billing*", new_name="Billing* (waiting)")
        dropped = await call(c, "rename_session", session="Billing* (waiting)", new_name="Billing*")
    assert added["name"] == "Billing* (waiting)" and dropped["name"] == "Billing*"


async def test_a_status_cannot_replace_part_of_a_name_the_owner_chose(tmp_path, root, live):
    session(live, root, "Billing* (v2)", source="user")
    async with Client(server(tmp_path, root, live)) as c:
        res = await c.call_tool("rename_session", {"session": "Billing* (v2)", "new_name": "Billing* (waiting)"})
    assert res.is_error and "owner chose" in res.content[0].text


async def test_a_name_renamed_at_the_owners_request_with_an_asterisk_is_the_owners(tmp_path, root, live):
    session(live, root, "fix the thing", source="auto")
    async with Client(server(tmp_path, root, live)) as c:
        await call(c, "rename_session", session="fix the thing", new_name="Billing*", owner_chose=True)
        res = await c.call_tool("rename_session", {"session": "Billing", "new_name": "other"})
    assert res.is_error and "owner chose" in res.content[0].text


def test_a_session_is_found_by_its_name_without_the_asterisk():
    # Said aloud, a name comes back without its asterisk; a message to "Coordinator" still reaches
    # "Coordinator*".
    rows = [{"name": "Session names upkeep*", "sessionId": "a"}, {"name": "Co*ordinator", "sessionId": "b"}]
    assert renames.find(rows, "session names upkeep")["sessionId"] == "a"
    assert renames.find(rows, "Session names upkeep*")["sessionId"] == "a"
    assert renames.find(rows, "Coordinator")["sessionId"] == "b"


async def test_a_name_that_differs_only_by_the_asterisk_is_in_use(tmp_path, root, live):
    session(live, root, "billing*")
    write(live, os.getppid(), "other-id", "Payroll", root)
    async with Client(server(tmp_path, root, live)) as c:
        res = await c.call_tool("rename_session", {"session": "Payroll", "new_name": "billing", "owner_chose": True})
    assert res.is_error and "in use" in res.content[0].text


async def test_an_agent_cannot_take_the_asterisk_off_with_a_status(tmp_path, root, live):
    # An asterisk inside the round brackets would otherwise go with the "status" an agent may drop.
    session(live, root, "Phone (mine*)")
    async with Client(server(tmp_path, root, live)) as c:
        res = await c.call_tool("rename_session", {"session": "Phone (mine*)", "new_name": "Phone"})
    assert res.is_error and "owner chose" in res.content[0].text


def test_a_session_s_own_name_is_found_before_another_s_old_name():
    # Once the Coordinator is "Coordinator*", a session that was once called "Coordinator" must not
    # take its messages.
    rows = [
        {"name": "Payroll", "sessionId": "b", "aliases": ["Coordinator"]},
        {"name": "Coordinator*", "sessionId": "a"},
    ]
    assert renames.find(rows, "Coordinator")["sessionId"] == "a"
    assert renames.find(rows, "Payroll")["sessionId"] == "b"


def test_a_session_not_running_is_found_by_the_newest_that_had_its_name(tmp_path):
    # config.toml wakes "Arborist" at each start; older sessions named exactly that must not win
    # over the one now called "Arborist*".
    store = Store(tmp_path / "b.db")
    store.record_started("old-id", "Arborist", str(tmp_path))
    record = tmp_path / "jobs" / "new" / "state.json"
    record.parent.mkdir(parents=True)
    newer = {"sessionId": "new-id", "name": "Arborist*", "cwd": str(tmp_path), "updatedAt": "2099-01-01T00:00:00Z"}
    record.write_text(json.dumps(newer), encoding="utf-8")
    assert every_session.known(store, "Arborist", tmp_path / "jobs")["claude_session_id"] == "new-id"
    assert every_session.known(store, "old-id", tmp_path / "jobs")["name"] == "Arborist"


@pytest.fixture
def world(tmp_path):
    """A folder to start sessions in, as tests/test_launch.py sets it up."""
    (tmp_path / "home" / "billing").mkdir(parents=True)
    (tmp_path / "live").mkdir()
    return tmp_path


async def test_a_started_session_keeps_the_name_given(world):
    starter = Starter(world / "live")
    async with Client(bridge(world, starter)) as c:
        out = (await c.call_tool("start_active_session", {"folder": "billing", "name": "inv"})).structured_content
    assert out["name"] == "inv"


class StarredStarter(Starter):
    """Starter writes its record under the session's name, and Windows takes no asterisk in a file name."""

    async def __call__(self, cwd, name, **options):
        self.calls.append(name)
        (self.live / f"{name.replace('*', '-')}.json").write_text(
            json.dumps({"pid": os.getpid(), "sessionId": f"sid-{name}", "cwd": cwd, "name": name, "status": "idle"})
        )


async def test_only_the_owner_starts_a_session_under_a_name_with_an_asterisk(world):
    starter = StarredStarter(world / "live")
    async with Client(bridge(world, starter)) as c:
        agents = await c.call_tool("start_active_session", {"folder": "billing", "name": "Inv*"})
        owners = (
            await c.call_tool("start_active_session", {"folder": "billing", "name": "Inv*", "owner_chose": True})
        ).structured_content
    assert agents.is_error and "asterisk" in agents.content[0].text
    assert owners["name"] == "Inv*" and starter.calls == ["Inv*"]


async def test_a_start_never_cuts_the_asterisk_off_a_name(world):
    starter = StarredStarter(world / "live")
    async with Client(bridge(world, starter)) as c:
        res = await c.call_tool(
            "start_active_session", {"folder": "billing", "name": "x" * 60 + "*", "owner_chose": True}
        )
    assert res.is_error and "asterisk" in res.content[0].text and starter.calls == []


def resuming(tmp_path, world, title):
    """A bridge that can resume one earlier conversation, whose name Claude Code keeps as `title`."""
    from claude_agent_sdk import SDKSessionInfo
    from fakes import FakeClaude
    from test_server import FakeConversations
    from test_start import OLD

    billing = world / "home" / "billing"
    m = SessionManager(Store(world / "b.db"), project_root=world / "home", client_factory=FakeClaude())
    talk = SDKSessionInfo(
        session_id=OLD, summary="hi", last_modified=0, cwd=str(billing), first_prompt="hi", custom_title=title
    )
    starter = StarredStarter(world / "live")
    return (
        build_server(
            m,
            conversations=FakeConversations([talk]),
            live_dir=world / "live",
            start_background=starter,
            start_seconds=1.0,
        ),
        starter,
        OLD,
    )


async def test_only_the_owner_resumes_a_session_under_a_name_with_an_asterisk(world):
    bridge_, starter, old = resuming(world, world, "Invoices")
    async with Client(bridge_) as c:
        res = await c.call_tool("attach_conversation", {"claude_session_id": old, "label": "Invoices*"})
    assert res.is_error and "asterisk" in res.content[0].text and starter.calls == []


async def test_an_agent_cannot_resume_the_owners_session_under_another_name(world):
    bridge_, starter, old = resuming(world, world, "Invoices*")
    async with Client(bridge_) as c:
        res = await c.call_tool("attach_conversation", {"claude_session_id": old, "label": "Payroll"})
    assert res.is_error and "owner chose" in res.content[0].text and starter.calls == []


async def rows(c):
    return {s["name"]: s for s in (await call(c, "list_active_sessions"))["sessions"]}


async def test_a_name_proposal_shows_on_the_sessions_row(tmp_path, root, live):
    session(live, root, "Billing", source="user")
    async with Client(server(tmp_path, root, live)) as c:
        out = await call(c, "propose_session_name", session="Billing", new_name="billing export", why="now exports")
        row = (await rows(c))["Billing"]
    assert out["status"] == "proposed" and out["name"] == "Billing"
    assert row["name_proposal"] == {"name": "billing export", "why": "now exports"}


async def test_a_name_proposal_ends_once_the_session_is_renamed(tmp_path, root, live):
    session(live, root, "Billing", source="user")
    async with Client(server(tmp_path, root, live)) as c:
        await call(c, "propose_session_name", session="Billing", new_name="billing export", why="now exports")
        await call(c, "rename_session", session="Billing", new_name="billing export", owner_chose=True)
        row = (await rows(c))["billing export"]
    assert "name_proposal" not in row


async def test_a_name_proposal_ends_after_a_day(tmp_path, root, live):
    session(live, root, "Billing", source="user")
    clock = Clock()
    async with Client(server(tmp_path, root, live, clock=clock)) as c:
        await call(c, "propose_session_name", session="Billing", new_name="billing export", why="now exports")
        clock.now += 24 * HOUR + 1
        row = (await rows(c))["Billing"]
    assert "name_proposal" not in row


async def test_a_name_proposal_outlasts_a_bridge_restart(tmp_path, root, live):
    session(live, root, "Billing", source="user")
    async with Client(server(tmp_path, root, live)) as c:
        await call(c, "propose_session_name", session="Billing", new_name="billing export", why="now exports")
    async with Client(server(tmp_path, root, live)) as c:
        row = (await rows(c))["Billing"]
    assert row["name_proposal"]["name"] == "billing export"
