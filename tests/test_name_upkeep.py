"""Keeping session names current: who chose a name, shown by a marker, and a check now and then.

A name an agent chose is in curly braces, so the owner sees it may change by itself. An agent
may rename such a session, and Claude Code's own automatic title; a name the owner chose is renamed
only at the owner's request (owner_chose), so a session only proposes one. About once an hour, the
note after a relayed message asks the session whether its name still fits."""

import json
import os

import pytest
from mcp.client import Client
from test_launch import Starter, bridge
from test_rename import Recorder, call, names, write

from claude_voice import renames
from claude_voice.server import RELAYED_MESSAGE_NOTE, build_server
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


async def test_a_name_an_agent_gives_gets_the_marker(tmp_path, root, live):
    session(live, root, "fix the thing", source="auto")
    async with Client(server(tmp_path, root, live)) as c:
        out = await call(c, "rename_session", session="fix the thing", new_name="billing export")
        assert await names(c) == ["{billing export}"]
    assert out["name"] == "{billing export}"


async def test_claude_code_s_own_title_may_be_renamed_by_an_agent(tmp_path, root, live):
    session(live, root, "Fix the export bug", source="auto")  # made from the first message
    async with Client(server(tmp_path, root, live)) as c:
        out = await call(c, "rename_session", session="Fix the export bug", new_name="billing export")
    assert out["name"] == "{billing export}"


async def test_a_name_already_marked_keeps_one_marker(tmp_path, root, live):
    session(live, root, "{billing export}")
    async with Client(server(tmp_path, root, live)) as c:
        out = await call(c, "rename_session", session="{billing export}", new_name="{billing import}")
    assert out["name"] == "{billing import}"


async def test_a_name_the_owner_chose_for_is_kept_as_given(tmp_path, root, live):
    session(live, root, "fix the thing", source="auto")
    async with Client(server(tmp_path, root, live)) as c:
        out = await call(c, "rename_session", session="fix the thing", new_name="Billing", owner_chose=True)
    assert out["name"] == "Billing"


async def test_an_agent_cannot_rename_a_session_the_owner_named(tmp_path, root, live):
    session(live, root, "Billing", source="user")
    async with Client(server(tmp_path, root, live)) as c:
        res = await c.call_tool("rename_session", {"session": "Billing", "new_name": "billing export"})
        assert await names(c) == ["Billing"]
    assert res.is_error
    assert "owner chose" in res.content[0].text and "propose_session_name" in res.content[0].text


async def test_a_name_the_owner_marked_themselves_is_free_to_change(tmp_path, root, live):
    session(live, root, "{Billing}", source="user")  # the owner added the braces with /rename
    async with Client(server(tmp_path, root, live)) as c:
        out = await call(c, "rename_session", session="{Billing}", new_name="billing export")
    assert out["name"] == "{billing export}"


async def test_a_name_renamed_at_the_owners_request_is_the_owners(tmp_path, root, live):
    session(live, root, "fix the thing", source="auto")
    async with Client(server(tmp_path, root, live)) as c:
        await call(c, "rename_session", session="fix the thing", new_name="Billing", owner_chose=True)
        res = await c.call_tool("rename_session", {"session": "Billing", "new_name": "other"})
    assert res.is_error and "owner chose" in res.content[0].text


def test_a_session_is_found_by_its_name_without_the_marker():
    # Said aloud, a name comes back without its marker.
    rows = [{"name": "{session names upkeep}", "sessionId": "a"}]
    assert renames.find(rows, "Session names upkeep")["sessionId"] == "a"
    assert renames.find(rows, "{session names upkeep}")["sessionId"] == "a"


async def test_a_name_that_differs_only_by_the_marker_is_in_use(tmp_path, root, live):
    session(live, root, "{billing}")
    write(live, os.getppid(), "other-id", "Payroll", root)
    async with Client(server(tmp_path, root, live)) as c:
        res = await c.call_tool("rename_session", {"session": "Payroll", "new_name": "billing", "owner_chose": True})
    assert res.is_error and "in use" in res.content[0].text


def checks(deliver):
    """For each message sent, the name check after the bridge's note, or None."""
    out = []
    for _, text in deliver.sent:
        after = text.split(RELAYED_MESSAGE_NOTE, 1)[1].strip()
        out.append(after or None)
    return out


async def test_the_name_check_comes_once_an_hour_on_the_next_relayed_message(tmp_path, root, live):
    session(live, root, "{billing}")
    deliver, clock = Recorder(), Clock()
    async with Client(server(tmp_path, root, live, deliver=deliver, clock=clock)) as c:
        await call(c, "message_active_session", session="{billing}", message="one")
        clock.now += HOUR - 60
        await call(c, "message_active_session", session="{billing}", message="two")
        clock.now += 120
        await call(c, "message_active_session", session="billing", message="three")
        clock.now += 60
        await call(c, "message_active_session", session="billing", message="four")
    first, second, third, fourth = checks(deliver)
    assert first is None and second is None and fourth is None
    assert '"{billing}"' in third and "rename_session" in third


async def test_the_name_check_clock_survives_a_bridge_restart(tmp_path, root, live):
    # The bridge restarts often; a session messaged in short bursts after each restart was never checked.
    session(live, root, "{billing}")
    deliver, clock = Recorder(), Clock()
    async with Client(server(tmp_path, root, live, deliver=deliver, clock=clock)) as c:
        await call(c, "message_active_session", session="{billing}", message="before the restart")
    clock.now += HOUR
    async with Client(server(tmp_path, root, live, deliver=deliver, clock=clock)) as c:
        await call(c, "message_active_session", session="{billing}", message="after the restart")
    assert checks(deliver)[1] is not None


async def test_the_name_check_asks_for_a_proposal_when_the_owner_chose_the_name(tmp_path, root, live):
    session(live, root, "Billing", source="user")
    deliver, clock = Recorder(), Clock()
    async with Client(server(tmp_path, root, live, deliver=deliver, clock=clock)) as c:
        await call(c, "message_active_session", session="Billing", message="one")
        clock.now += HOUR
        await call(c, "message_active_session", session="Billing", message="two")
    check = checks(deliver)[1]
    assert "the owner chose" in check and "propose_session_name" in check
    assert "rename_session" not in check


async def test_a_relayed_message_with_a_name_check_still_reads_as_one_line(tmp_path, root, live):
    session(live, root, "{billing}")
    deliver, clock = Recorder(), Clock()
    async with Client(server(tmp_path, root, live, deliver=deliver, clock=clock)) as c:
        await call(c, "message_active_session", session="{billing}", message="one")
        clock.now += HOUR
        await call(c, "message_active_session", session="{billing}", message="two")
    assert deliver.sent[1][1].startswith("two\n\n" + RELAYED_MESSAGE_NOTE)


@pytest.fixture
def world(tmp_path):
    """A folder to start sessions in, as tests/test_launch.py sets it up."""
    (tmp_path / "home" / "billing").mkdir(parents=True)
    (tmp_path / "live").mkdir()
    return tmp_path


async def test_a_started_session_gets_the_marker_unless_the_owner_named_it(world):
    starter = Starter(world / "live")
    async with Client(bridge(world, starter)) as c:
        agents = (await c.call_tool("start_active_session", {"folder": "billing", "name": "inv"})).structured_content
        owners = (
            await c.call_tool("start_active_session", {"folder": "billing", "name": "Inv", "owner_chose": True})
        ).structured_content
    assert agents["name"] == "{inv}" and owners["name"] == "Inv"


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


async def test_no_name_check_while_a_proposal_waits_for_the_owner(tmp_path, root, live):
    # A proposal the owner turned down would come back every hour otherwise.
    session(live, root, "Billing", source="user")
    deliver, clock = Recorder(), Clock()
    async with Client(server(tmp_path, root, live, deliver=deliver, clock=clock)) as c:
        await call(c, "message_active_session", session="Billing", message="one")
        await call(c, "propose_session_name", session="Billing", new_name="payroll", why="work moved")
        clock.now += HOUR
        await call(c, "message_active_session", session="Billing", message="two")
        clock.now += 24 * HOUR
        await call(c, "message_active_session", session="Billing", message="three")
    first, second, third = checks(deliver)
    assert second is None and third is not None
