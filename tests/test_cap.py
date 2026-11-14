"""No tool call waits past the caller's time budget: the Claude app drops a call after about
30 seconds, and is given 20.

Past the budget a call says the work is still going and how to read on. The side session has
the same three ways in as an open session: send and return, read later, ask and wait.
"""

import asyncio
import json
import logging
import os
import time

import pytest
from fakes import FakeClaude, Pause, init, result, say
from mcp.client import Client
from test_ask import Target

from claude_voice import config
from claude_voice import server as bridge_server
from claude_voice.server import build_server, caller_kind, log_requests
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

CAP = 0.3


@pytest.fixture
def root(tmp_path):
    (tmp_path / "src" / "billing").mkdir(parents=True)
    return tmp_path / "src"


@pytest.fixture
def live(tmp_path):
    d = tmp_path / "live"
    d.mkdir()
    return d


def bridge(tmp_path, root, live, claude, target=None, deliver=None):
    async def delivered(name, text):
        return {"delivered": True, "detail": "DELIVERED"}

    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=claude)
    return m, build_server(
        m,
        conversations=lambda **k: [],
        live_dir=live,
        deliver=deliver or delivered,
        read_transcript=(lambda sid, directory: list(target.transcript)) if target else None,
        poll_seconds=0.01,
        projects_dir=tmp_path / "projects",
        repo_config=config.Config(
            config.Repos(), config.Paths(None, None, None, None), None, budgets=config.Budgets({"default": CAP})
        ),
    )


async def call(client, name, **args):
    res = await client.call_tool(name, args)
    assert not res.is_error, res.content
    return res.structured_content


def desk(live, root):
    """A running session for the fork tests to fork."""
    entry = {"pid": os.getpid(), "sessionId": "desk-1", "cwd": str(root / "billing"), "name": "billing-ab"}
    (live / f"{os.getpid()}.json").write_text(json.dumps(entry), encoding="utf-8")


async def test_every_waiting_call_stops_at_the_callers_time_budget(tmp_path, root, live):
    target = Target(live, str(root / "billing"), status="busy")  # never answers
    claude = FakeClaude([init("f-1"), Pause()])
    _, srv = bridge(tmp_path, root, live, claude, target=target)
    async with Client(srv) as c:
        started = time.monotonic()
        asked = await call(c, "ask_active_session", session="billing-ab", message="?", wait_seconds=120)
        forked = await call(c, "ask_forked_session", session="billing-ab", question="?", wait_seconds=120)
        read = await call(c, "read_forked_session", fork_id=forked["fork_id"], wait_seconds=120)
        elapsed = time.monotonic() - started
    assert asked["status"] == "still_working"
    assert forked["status"] == "still_working" and forked["fork_id"]
    assert read["status"] == "still_working"
    assert elapsed < 3 * CAP + 2  # three calls, each within the budget


@pytest.mark.parametrize("forwarded", [[], [(b"x-forwarded-for", b"203.0.113.7")]])
def test_a_call_through_the_owners_server_proxy_gets_the_claude_apps_budget(forwarded):
    # The Claude app reaches this fork's bridge through a reverse proxy on another computer,
    # over a private link: the request comes from the proxy's address, naming the real caller
    # in X-Forwarded-For or not. Either way it is the Claude app, held to its budget.
    class Request:
        scope = {"client": ("10.0.0.1", 50123), "headers": forwarded}

    class Ctx:
        request = Request()

    kind = caller_kind(Ctx(), None)
    assert kind == "claude_app" and config.Budgets().seconds(kind) == config.BUDGETS["claude_app"]


async def test_a_sent_message_says_where_to_read_its_answer_from(tmp_path, root, live):
    target = Target(live, str(root / "billing"))
    _, srv = bridge(tmp_path, root, live, FakeClaude(), target=target)
    async with Client(srv) as c:
        out = await call(c, "message_active_session", session="billing-ab", message="status?")
    assert out["delivered"] is True
    assert out["next_after"] == 1  # the two turns already there: read from after the second


async def test_a_fork_still_working_at_the_cap_is_read_later(tmp_path, root, live):
    desk(live, root)
    pause = Pause()
    claude = FakeClaude([init("f-1"), say("Looking."), pause, result("In billing.py.", "f-1")])
    _, srv = bridge(tmp_path, root, live, claude)
    async with Client(srv) as c:
        first = await call(c, "ask_forked_session", session="billing-ab", question="Where?", wait_seconds=60)
        assert first["status"] == "still_working"
        again = await call(c, "read_forked_session", fork_id=first["fork_id"], wait_seconds=0)
        assert again["status"] == "still_working"
        pause.release.set()
        done = await call(c, "read_forked_session", fork_id=first["fork_id"], wait_seconds=60)
    assert done["status"] == "answered" and done["answer"] == "In billing.py."


async def test_a_message_to_a_fork_returns_at_once_and_is_read_later(tmp_path, root, live):
    desk(live, root)
    claude = FakeClaude([init("f-1"), result("Done reading.", "f-1")])
    _, srv = bridge(tmp_path, root, live, claude)
    async with Client(srv) as c:
        sent = await call(c, "message_forked_session", session="billing-ab", question="Look at it.")
        assert sent["status"] == "started" and sent["fork_id"]
        done = await call(c, "read_forked_session", fork_id=sent["fork_id"], wait_seconds=60)
    assert done["status"] == "answered" and done["answer"] == "Done reading."


async def test_an_unknown_fork_says_forks_are_forgotten_on_restart(tmp_path, root, live):
    _, srv = bridge(tmp_path, root, live, FakeClaude())
    async with Client(srv) as c:
        res = await c.call_tool("read_forked_session", {"fork_id": "nope"})
    assert res.is_error and "restart" in res.content[0].text


async def test_a_call_the_client_drops_is_logged_with_how_long_it_ran(caplog):
    class Ctx:
        method = "tools/call"
        params = {"name": "ask_active_session"}
        request_id = 1

    async def dropped(ctx):
        await asyncio.sleep(0.01)
        raise asyncio.CancelledError

    with caplog.at_level(logging.INFO, logger="claude_voice"), pytest.raises(asyncio.CancelledError):
        await log_requests(Ctx(), dropped)
    assert any("ask_active_session" in r.message and "cancelled" in r.message for r in caplog.records)


async def test_a_question_s_wait_counts_from_the_call_delivery_included(tmp_path, root, live):
    # A question left without a length waits to the caller's limit; counted from the delivery, a
    # slow courier (33 s seen on 2026-11-13) or a resume would push the whole call past it, and
    # past the 240 s claude.ai gives a call (the branch's review, 2026-11-14).
    target = Target(live, str(root / "billing"), status="busy")  # never answers

    async def slow(name, text):
        await asyncio.sleep(CAP)
        return {"delivered": True, "detail": "DELIVERED"}

    _, srv = bridge(tmp_path, root, live, FakeClaude(), target=target, deliver=slow)
    async with Client(srv) as c:
        started = time.monotonic()
        out = await call(c, "ask_active_session", session="billing-ab", message="Is the export done?")
        took = time.monotonic() - started
    assert out["status"] == "still_working"
    assert took < CAP * 1.6  # the delivery took the whole limit: no wait after it


async def test_no_tool_that_waits_sets_a_length_of_its_own(tmp_path, root, live):
    # Left out, every wait runs to the caller's limit, tested in test_news.py. A short default,
    # meant to come back before the Claude app sends a call to the background at about 10 s,
    # rarely did: the app counts those seconds across the turn, and an ask's delivery came first.
    # Its "still_working" then woke the relay only to wait again (bridge log, 2026-11-06 to 14).
    _, server = bridge(tmp_path, root, live, FakeClaude())
    async with Client(server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    for name in ("wait_for_session", "ask_active_session", "ask_forked_session", "read_forked_session"):
        assert tools[name].input_schema["properties"]["wait_seconds"].get("default") is None, name


async def test_a_result_that_took_a_while_says_where_a_late_one_comes_from(tmp_path, root, live, monkeypatch):
    # Voice mode moves a call to the background when the owner talks over it, and delivers the
    # result later inside a call the assistant seems to have written, with a field only the app
    # may write; assistants copy it. A slow result carries a reminder, a quick one doesn't.
    monkeypatch.setattr(bridge_server, "LATE_SECONDS", 0.1)
    target = Target(live, str(root / "billing"), status="busy")
    _, srv = bridge(tmp_path, root, live, FakeClaude(), target=target)
    async with Client(srv) as c:
        slow = await c.call_tool("wait_for_session", {"session": "billing-ab", "wait_seconds": 60})
        quick = await call(c, "list_active_sessions")
    assert "_deferred_result_for" in slow.structured_content["late"]
    assert "_deferred_result_for" in slow.content[0].text
    assert "late" not in quick


async def test_a_call_made_while_a_wait_on_its_session_runs_says_the_wait_returns_by_itself(tmp_path, root, live):
    # A call the owner talked over keeps running; a relay that calls again on the same session
    # meanwhile is told its pending result arrives by itself.
    target = Target(live, str(root / "billing"), status="busy")
    _, srv = bridge(tmp_path, root, live, FakeClaude(), target=target)
    async with Client(srv) as c:
        waiting = asyncio.create_task(call(c, "wait_for_session", session="billing-ab", wait_seconds=60))
        await asyncio.sleep(CAP / 3)
        during = await call(c, "read_session_output", session="billing-ab")
        first = await waiting
        after = await call(c, "read_session_output", session="billing-ab")
    assert "arrives by itself" in during["pending"]
    assert "pending" not in first and "pending" not in after
