"""A news line on every result: how the sessions the relay watches stand, without a call of its own.

With several sessions running, a relay misses replies. Counting every unread message turned into
noise when one session posted many progress lines, and the relay stopped reading the line; a set
of every session ever touched went stale. So the relay follows a session from its message or
question (or a hand-off, follow_session) until it has read the turn that answers it, and only
three things are news: a finished turn it hasn't read, a waiting permission prompt, and a followed
session having ended. Every followed session gets a short entry even with nothing new, so
"nothing new" can't be mistaken for "not followed". A permission prompt shows for every session,
followed or not, since only the owner can answer it. Reading a session only moves the relay's read
mark; what the owner typed in a session is said when the relay next writes to it.
"""

import asyncio
import contextlib
import json
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
from fakes import FakeClaude
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from test_server import TOKEN, free_port

from claude_voice import config
from claude_voice.oauth import OAuthProvider
from claude_voice.server import build_app, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store
from claude_voice.transcripts import project_key_for_directory

PID = os.getpid()


def turn(role, text, origin=None):
    content = text if role == "user" else [{"type": "text", "text": text}]
    return SimpleNamespace(type=role, message={"content": content}, error=None, origin=origin)


class Desk:
    """A running session: its registry entry and its conversation."""

    def __init__(self, live, cwd, name="billing-ab", sid="sid-1", pid=PID):
        self.live, self.cwd, self.name, self.sid, self.pid = live, cwd, name, sid, pid
        self.transcript = [turn("user", "earlier question", "human"), turn("assistant", "earlier answer")]
        self.set("idle")

    def set(self, status, waiting_for=None):
        entry = {"pid": self.pid, "sessionId": self.sid, "cwd": self.cwd, "name": self.name, "status": status}
        if waiting_for:
            entry["waitingFor"] = waiting_for
        (self.live / f"{self.name}.json").write_text(json.dumps(entry), encoding="utf-8")

    def gone(self):
        (self.live / f"{self.name}.json").unlink()


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "work"
    (root / "billing").mkdir(parents=True)
    live = tmp_path / "live"
    live.mkdir()
    return root, live


@contextlib.asynccontextmanager
async def bridge(tmp_path, desk, store, *others, **options):
    """The bridge over HTTP, as the Claude app reaches it, for one desk session or more."""
    root = Path(desk.cwd).parent
    desks = {d.sid: d for d in (desk, *others)}

    async def deliver(name, text):
        # The relayed message lands in the session's conversation, marked as from another session.
        next(d for d in desks.values() if d.name == name).transcript.append(turn("user", text, "peer"))
        return {"delivered": True, "detail": "DELIVERED"}

    if not any(store.get_setting(k) for k in ("relay_handles", "read_marks", "watched_sessions")):
        # The handles the tests pass, as if the bridge had handed them out.
        store.set_setting("relay_handles", {h: {"used": time.time(), "sessions": {}} for h in KNOWN})
    port = free_port()
    oauth = OAuthProvider(store, TOKEN, f"http://127.0.0.1:{port}", static_token=TOKEN)
    m = SessionManager(store, project_root=root, client_factory=FakeClaude())
    server = build_server(
        m,
        oauth=oauth,
        conversations=lambda **k: [],
        live_dir=desk.live,
        deliver=deliver,
        read_transcript=lambda sid, directory: list(desks[sid].transcript),
        poll_seconds=0.01,
        projects_dir=tmp_path / "projects",
        repo_config=config.Config(
            config.Repos(), config.Paths(None, None, None, None), None, budgets=config.Budgets({"default": 0.3})
        ),
        **options,
    )
    uv = uvicorn.Server(uvicorn.Config(build_app(server), port=port, log_level="warning"))
    task = asyncio.create_task(uv.serve())
    while not uv.started:  # noqa: ASYNC110 - uvicorn exposes no event
        await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        uv.should_exit = True
        await task


@pytest.fixture
async def served(tmp_path, world):
    """The bridge with one desk session."""
    root, live = world
    desk = Desk(live, str(root / "billing"))
    async with bridge(tmp_path, desk, Store(tmp_path / "bridge.db")) as url:
        yield url, desk


def conversation(url):
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {TOKEN}"})
    return http, Client(streamable_http_client(f"{url}/mcp", http_client=http), mode="legacy")


# The tools that take the relay's handle; the helper passes HANDLE to them unless a test says otherwise.
HANDLED = (
    "message_active_session",
    "ask_active_session",
    "read_session_output",
    "wait_for_session",
    "follow_session",
    "list_active_sessions",
)
HANDLE = "Juniper"
KNOWN = (HANDLE, "Rowan", "@kQzw")


async def call(client, name, **args):
    if name in HANDLED and "handle" not in args:
        args["handle"] = HANDLE
    args = {k: v for k, v in args.items() if v is not None}
    res = await client.call_tool(name, args)
    assert not res.is_error, res.content
    return res.structured_content


async def test_following_a_session_ends_once_its_finished_turn_is_read(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        sent = await call(c, "message_active_session", session="billing-ab", message="Run the export.")
        desk.transcript.append(turn("assistant", "Export done."))
        later = await call(c, "list_active_sessions")
        read = await call(c, "read_session_output", session="billing-ab", after=sent["next_after"])
        after = await call(c, "list_active_sessions")
    assert later["news"] == "[Juniper] billing-ab (idle): reply to you, read with after=1"
    assert list(later)[-1] != "news" and "news" in list(later)[:3]  # given before the result itself
    assert "news" not in read and "news" not in after  # read: following it ends


async def test_reading_a_session_does_not_follow_it(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "read_session_output", session="billing-ab")
        desk.set("busy")
        desk.transcript.append(turn("assistant", "Working on something else."))
        desk.set("idle")
        out = await call(c, "list_active_sessions")
    assert "news" not in out


async def test_a_reply_to_a_session_with_no_turns_yet_is_news_too(served):
    # Seen live: a relay started a session with no first message, then sent its brief; the
    # send returned next_after -1, and the reply never showed as news.
    url, desk = served
    desk.transcript = []
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "read_session_output", session="billing-ab")  # as start_active_session marks it
        sent = await call(c, "message_active_session", session="billing-ab", message="Here is your brief.")
        desk.transcript.append(turn("assistant", "Report: all done."))
        later = await call(c, "list_active_sessions")
    assert sent["next_after"] == -1
    assert later["news"] == "[Juniper] billing-ab (idle): reply to you, read with after=-1"


async def test_a_wait_without_after_gives_the_relay_all_it_has_not_read(served):
    # The owner's call: a wait never skips what the relay hasn't seen, so without `after` it
    # starts where the relay last read the session, not where the transcript ends.
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Run the export.")
        desk.transcript.append(turn("assistant", "Starting the export."))
        desk.transcript.append(turn("assistant", "Export done."))
        out = await call(c, "wait_for_session", session="billing-ab")
    assert [t["text"] for t in out["turns"]] == ["Starting the export.", "Export done."]
    assert out["reply"] == "Export done." and out["reply_final"] is True


async def test_a_wait_without_after_on_a_session_already_read_brings_nothing_old(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        sent = await call(c, "message_active_session", session="billing-ab", message="Run the export.")
        desk.transcript.append(turn("assistant", "Export done."))
        await call(c, "read_session_output", session="billing-ab", after=sent["next_after"])
        out = await call(c, "wait_for_session", session="billing-ab")
    assert out["status"] == "idle" and out["reply"] == "" and out["turns"] == []


async def test_progress_while_the_session_works_is_not_news(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Run the export.")
        desk.set("busy")
        desk.transcript.append(turn("assistant", "Exporting the first table."))
        desk.transcript.append(turn("assistant", "Exporting the second table."))
        working = await call(c, "list_active_sessions")
        desk.transcript.append(turn("assistant", "Export done."))
        desk.set("idle")
        done = await call(c, "list_active_sessions")
    assert working["news"] == "[Juniper] billing-ab (busy): nothing new"
    assert done["news"] == "[Juniper] billing-ab (idle): reply to you, read with after=1"


async def test_a_question_still_unanswered_when_ask_returns_is_a_reply_later(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        asked = await call(c, "ask_active_session", session="billing-ab", message="Is the export done?")
        desk.transcript.append(turn("assistant", "Yes."))
        out = await call(c, "list_active_sessions")
    assert asked["status"] == "still_working"
    assert out["news"] == f"[Juniper] billing-ab (idle): reply to you, read with after={asked['next_after']}"


async def test_a_send_to_a_session_the_owner_typed_in_since_the_last_read_says_so(served):
    # Said again when the relay writes to that session; the turn it started is also news while the
    # session is in a quiet follow (owner, 2026-11-13).
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        sent = await call(c, "message_active_session", session="billing-ab", message="Check the script.")
        desk.transcript.append(turn("assistant", "Checked."))
        await call(c, "read_session_output", session="billing-ab", after=sent["next_after"])
        desk.transcript.append(turn("user", "I ran it myself.", "human"))  # on the phone, directly
        desk.transcript.append(turn("assistant", "Then it is done."))
        quiet = await call(c, "list_active_sessions")
        again = await call(c, "message_active_session", session="billing-ab", message="And the invoices?")
    assert quiet["news"] == "[Juniper] billing-ab (idle): new turn, started by the owner typing, read with after=3"
    assert "read_session_output(after=3)" in again["owner_typed"]


async def test_only_what_the_owner_typed_counts_as_theirs(served):
    # Seen live: a background task's completion note arrives as a user turn too, and was
    # counted as typed by the owner. Only a turn Claude Code marks "human" is the owner's.
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        sent = await call(c, "message_active_session", session="billing-ab", message="Go.")
        desk.transcript.append(turn("assistant", "Gone."))
        await call(c, "read_session_output", session="billing-ab", after=sent["next_after"])
        desk.transcript.append(turn("user", "<task-notification> done </task-notification>", "task-notification"))
        desk.transcript.append(turn("user", "From another session.", "peer"))
        desk.transcript.append(turn("user", "no origin recorded", None))
        desk.transcript.append(turn("assistant", "Noted."))
        out = await call(c, "message_active_session", session="billing-ab", message="Next.")
    assert "owner_typed" not in out


async def test_a_session_waiting_on_a_permission_prompt_is_news_even_unfollowed(served):
    url, desk = served
    desk.set("waiting", waiting_for="permission prompt")
    http, client = conversation(url)
    async with http, client as c:
        out = await call(c, "list_active_sessions")
    assert out["news"] == "[Juniper] billing-ab: waiting on a permission prompt"


async def test_a_followed_session_that_ended_says_so_once_then_leaves_the_line(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Wrap up.")
        desk.gone()
        ended = await call(c, "list_active_sessions")
        later = await call(c, "list_active_sessions")
    assert ended["news"] == "[Juniper] billing-ab: ended"  # it can't be read any more
    assert "news" not in later


async def test_a_session_that_ended_before_its_reply_was_read_says_so(served):
    # It can't be read any more; the relay can still resume it with attach_conversation.
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Wrap up.")
        desk.transcript.append(turn("assistant", "Wrapped up."))
        desk.gone()
        out = await call(c, "list_active_sessions")
    assert out["news"] == "[Juniper] billing-ab: ended before you read its reply"


def closed_for_memory(path, short):
    """Claude Code's background manager closing a session to free memory, as its log says it."""
    path.write_text(
        f"[2026-11-13T00:19:50.365Z] [bg] bg retire {short}: settled, idle 2h [low memory]\n"
        f"[2026-11-13T00:19:50.969Z] [bg] bg settled {short} (done)\n",
        encoding="utf-8",
    )
    return path


async def test_a_session_claude_code_closed_for_low_memory_says_why_and_how_to_resume_it(tmp_path, world):
    # Seen 2026-11-13: eight sessions left the list at once and the relay was told nothing.
    root, live = world
    desk = Desk(live, str(root / "billing"))
    log = closed_for_memory(tmp_path / "daemon.log", "sid-1")
    store = Store(tmp_path / "bridge.db")
    store.record_started("sid-1", "billing-ab", desk.cwd)
    async with bridge(tmp_path, desk, store, daemon_log=log) as url:
        http, client = conversation(url)
        async with http, client as c:
            await call(c, "message_active_session", session="billing-ab", message="Wrap up.")
            desk.gone()
            out = await call(c, "list_active_sessions")
            # A message would resume it (test_start); a read says why it can't.
            again = await c.call_tool("read_session_output", {"session": "billing-ab", "handle": HANDLE})
    assert out["news"] == (
        "[Juniper] billing-ab: ended, closed by Claude Code for low memory; attach_conversation resumes it"
    )
    assert again.is_error
    text = again.content[0].text
    assert "closed by Claude Code for low memory" in text
    assert "attach_conversation with claude_session_id sid-1" in text


async def test_a_session_that_is_not_running_points_to_attach_conversation(served):
    url, desk = served
    desk.gone()
    http, client = conversation(url)
    async with http, client as c:
        out = await c.call_tool("message_active_session", {"session": "billing-ab", "message": "Hi", "handle": HANDLE})
    assert out.is_error and "attach_conversation" in out.content[0].text


async def test_two_sessions_are_set_apart_in_one_line(served):
    url, desk = served
    other = Desk(desk.live, desk.cwd, name="ledger, cut-over", sid="sid-2")
    other.set("waiting", waiting_for="permission prompt")
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Go.")
        desk.transcript.append(turn("assistant", "Gone."))
        out = await call(c, "list_active_sessions")
    news = "[Juniper] billing-ab (idle): reply to you, read with after=1"
    assert out["news"] == f"{news} | ledger, cut-over: waiting on a permission prompt"


async def test_a_session_handed_over_is_followed_until_any_turn_it_finishes(served):
    # "Tell me when it's done": the owner hands over a session they are driving themselves, so a
    # turn answering their own typing counts.
    url, desk = served
    desk.set("busy")
    http, client = conversation(url)
    async with http, client as c:
        handed = await call(c, "follow_session", session="billing-ab")
        working = await call(c, "list_active_sessions")
        desk.transcript.append(turn("user", "Start on the invoices.", "human"))
        desk.transcript.append(turn("assistant", "Invoices done."))
        desk.set("idle")
        done = await call(c, "list_active_sessions")
    assert handed["status"] == "following"
    assert working["news"] == "[Juniper] billing-ab (busy): nothing new"  # what came before isn't news
    assert done["news"] == "[Juniper] billing-ab (idle): reply to you, read with after=1"


async def test_a_session_handed_over_at_rest_is_already_done_not_followed(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        handed = await call(c, "follow_session", session="billing-ab")
        out = await call(c, "list_active_sessions")
    assert handed["status"] == "already_done"
    assert "news" not in out


async def test_following_can_be_stopped(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Run the export.")
        stopped = await call(c, "follow_session", session="billing-ab", follow=False)
        desk.transcript.append(turn("assistant", "Export done."))
        out = await call(c, "list_active_sessions")
    assert stopped["status"] == "not_followed"
    assert "news" not in out


async def test_a_new_conversation_and_a_restarted_bridge_follow_the_same_sessions(tmp_path, world):
    # The relay is one role across calls: after a bridge restart, which leaves the voice call
    # running, or in a new call, it still needs the reply to the message sent before.
    root, live = world
    desk = Desk(live, str(root / "billing"))
    async with bridge(tmp_path, desk, Store(tmp_path / "bridge.db")) as url:
        http, client = conversation(url)
        async with http, client as c:
            await call(c, "message_active_session", session="billing-ab", message="Run the export.")
    desk.transcript.append(turn("assistant", "Export done."))
    async with bridge(tmp_path, desk, Store(tmp_path / "bridge.db")) as url:
        http, client = conversation(url)
        async with http, client as c:
            out = await call(c, "list_active_sessions")
    assert out["news"] == "[Juniper] billing-ab (idle): reply to you, read with after=1"


async def test_what_the_bridge_kept_under_the_old_name_is_still_followed(tmp_path, world):
    # The marks were kept as "watched_sessions" before following replaced watching.
    root, live = world
    desk = Desk(live, str(root / "billing"))
    store = Store(tmp_path / "bridge.db")
    store.set_setting("watched_sessions", {"sid-1": {"seen": 1, "sent": 1, "replied": False, "name": "billing-ab"}})
    desk.transcript.append(turn("assistant", "Export done."))
    async with bridge(tmp_path, desk, store) as url:
        http, client = conversation(url)
        async with http, client as c:
            out = await call(c, "list_active_sessions", handle="voice relay")
    assert out["news"] == "[voice relay] billing-ab (idle): reply to you, read with after=1"


async def test_nothing_followed_and_no_prompt_means_no_news_field(served):
    url, _ = served
    http, client = conversation(url)
    async with http, client as c:
        out = await call(c, "list_active_sessions")
    assert "news" not in out


async def test_a_reply_stays_news_when_the_relay_sends_again_before_reading_it(served):
    # Seen live (F36): the relay sent several messages in a row without reading; a report ended
    # between two of them, and every send said "nothing new", since a send marked all before it
    # read. Only a read marks a reply read, and a reply followed by a later message has ended.
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        first = await call(c, "message_active_session", session="billing-ab", message="How big is it?")
        desk.transcript.append(turn("assistant", "Round 5 done; three items wait on the owner."))
        desk.set("busy")
        again = await call(c, "message_active_session", session="billing-ab", message="Move it to Archive.")
        later = await call(c, "list_active_sessions")
    assert again["next_after"] == 3  # the report is turn 3, before the new message
    news = f"[Juniper] billing-ab (busy): reply to you, read with after={first['next_after']}"
    assert again["news"] == later["news"] == news


async def test_after_a_read_a_new_send_waits_only_for_its_own_reply(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        first = await call(c, "message_active_session", session="billing-ab", message="Run the export.")
        desk.transcript.append(turn("assistant", "Export done."))
        await call(c, "read_session_output", session="billing-ab", after=first["next_after"])
        desk.set("busy")
        again = await call(c, "message_active_session", session="billing-ab", message="Now the invoices.")
        desk.transcript.append(turn("assistant", "Invoices done."))
        desk.set("idle")
        done = await call(c, "list_active_sessions")
    assert again["news"] == "[Juniper] billing-ab (busy): nothing new"
    assert done["news"] == f"[Juniper] billing-ab (idle): reply to you, read with after={again['next_after']}"


# The next step, at the end of a result: the relay slipped on the guide's Waiting rules (offering
# to check instead of checking, stopping the wait early; ledger F58), so the result says the
# situation and what follows, in the states where something does.


async def test_a_session_still_on_the_relays_message_says_to_wait_again(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Run the export.")
        desk.set("busy")
        desk.transcript.append(turn("assistant", "Exporting the second table."))
        out = await call(c, "list_active_sessions")
    step = out["next_step"]
    assert step.startswith("billing-ab has been on your message for ")
    assert '(latest: "Exporting the second table.")' in step
    assert "call wait_for_session again now" in step
    assert list(out)[-1] == "next_step"  # after the result itself


async def test_a_finished_turn_not_yet_read_says_to_read_it(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Run the export.")
        desk.transcript.append(turn("assistant", "Export done."))
        out = await call(c, "list_active_sessions")
    assert out["next_step"].startswith("billing-ab has finished its turn: read it")


async def test_a_session_paused_on_the_owner_is_told_once_not_waited_on(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Delete the old exports.")
        desk.set("waiting", waiting_for="permission prompt")
        first = await call(c, "list_active_sessions")
        second = await call(c, "list_active_sessions")
    assert first["next_step"].startswith("billing-ab is paused on a permission prompt only the owner can answer")
    assert "next_step" not in second
    assert second["news"] == "[Juniper] billing-ab: waiting on a permission prompt"  # still named until answered


async def test_the_result_where_following_ends_says_there_is_nothing_left_to_follow(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        sent = await call(c, "message_active_session", session="billing-ab", message="Run the export.")
        desk.transcript.append(turn("assistant", "Export done."))
        await call(c, "list_active_sessions")
        read = await call(c, "read_session_output", session="billing-ab", after=sent["next_after"])
        later = await call(c, "list_active_sessions")
    assert read["next_step"] == "Nothing left to follow: every reply is read. No need to tell the owner."
    assert "next_step" not in later


async def test_nothing_followed_means_no_next_step(served):
    url, _ = served
    http, client = conversation(url)
    async with http, client as c:
        out = await call(c, "list_active_sessions")
    assert "next_step" not in out


# One wait for all followed sessions: wait_for_session with no session named.


async def test_a_wait_on_all_followed_sessions_returns_at_the_first_with_news(tmp_path, world):
    root, live = world
    desk = Desk(live, str(root / "billing"))
    other = Desk(live, str(root / "billing"), name="ledger", sid="sid-2", pid=os.getppid())  # its own process
    async with bridge(tmp_path, desk, Store(tmp_path / "bridge.db"), other) as url:
        http, client = conversation(url)
        async with http, client as c:
            await call(c, "message_active_session", session="billing-ab", message="Run the export.")
            await call(c, "message_active_session", session="ledger", message="Close the month.")
            desk.set("busy")
            other.transcript.append(turn("assistant", "Month closed."))
            out = await call(c, "wait_for_session")
            later = await call(c, "list_active_sessions")
    assert out["session"] == "ledger" and out["reply"] == "Month closed." and out["reply_final"] is True
    assert later["news"] == "[Juniper] billing-ab (busy): nothing new"  # ledger's reply was read: no longer followed


async def test_a_wait_on_all_followed_sessions_runs_out_while_they_work(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Run the export.")
        desk.set("busy")
        out = await call(c, "wait_for_session")
    assert out["status"] == "still_working" and out["sessions"] == ["billing-ab"]
    assert "call wait_for_session again now" in out["next_step"]


async def test_a_wait_with_nothing_to_wait_for_returns_at_once_and_says_so(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        out = await call(c, "wait_for_session", wait_seconds=60)
    assert out["status"] == "nothing_to_wait_for"
    assert out["next_step"] == "Nothing left to follow: every reply is read. No need to tell the owner."


async def test_a_session_paused_on_the_owner_wakes_a_wait_once(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Delete the old exports.")
        desk.set("waiting", waiting_for="permission prompt")
        first = await call(c, "wait_for_session")
        second = await call(c, "wait_for_session", wait_seconds=60)
    assert first["session"] == "billing-ab" and first["status"] == "needs_input"
    assert second["status"] == "nothing_to_wait_for"
    assert "billing-ab waits on the owner's answer" in second["next_step"]


async def test_a_wait_with_no_length_given_runs_to_the_caller_s_limit(served):
    # The long wait is the plan in the Claude app: it goes to the background and its result wakes
    # the relay, so the relay shouldn't have to pick a length (owner, 2026-11-11).
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        await call(c, "message_active_session", session="billing-ab", message="Run the export.")
        desk.set("busy")
        started = asyncio.get_running_loop().time()
        out = await call(c, "wait_for_session", session="billing-ab")
        took = asyncio.get_running_loop().time() - started
    assert tools["wait_for_session"].input_schema["properties"]["wait_seconds"].get("default") is None
    assert out["status"] == "still_working" and took >= 0.3  # the test bridge's limit is 0.3 s


def started_in_background(task_id):
    """The tool result that starts a task in the background, as the session's transcript keeps it."""
    out = turn("user", "")
    out.message = {"content": []}
    out.task, out.at = task_id, None
    return out


def task_done(task_id):
    """The completion note of a task, as the desktop app hands it to the session: a turn of its own."""
    out = turn("user", f"<task-notification>\n<task-id>{task_id}</task-id>\n</task-notification>", "task-notification")
    out.ended = [task_id]
    return out


async def test_a_session_with_a_background_task_still_running_is_not_at_rest(served):
    # Its turn has ended, but the task's completion note will start another: more is to come.
    url, desk = served
    desk.transcript += [started_in_background("bcw"), turn("assistant", "I'll say done when it finishes.")]
    http, client = conversation(url)
    async with http, client as c:
        handed = await call(c, "follow_session", session="billing-ab")
        out = await call(c, "list_active_sessions")
    assert handed["status"] == "following"
    assert out["news"] == "[Juniper] billing-ab (turn ended, background task still running): nothing new"
    assert out["next_step"].startswith("billing-ab ended its turn with a background task still running")
    assert "call wait_for_session again now" in out["next_step"]


async def test_a_background_task_s_completion_note_is_not_a_later_message(served):
    # The note starts the turn that finishes the work; until that turn ends, the reply isn't in.
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Sleep 90 s, then say done.")
        desk.transcript += [started_in_background("bcw"), turn("assistant", "I'll say done when it finishes.")]
        running = await call(c, "wait_for_session", wait_seconds=0.1)
        desk.transcript.append(task_done("bcw"))
        desk.set("busy")
        noted = await call(c, "wait_for_session", wait_seconds=0.1)
        desk.transcript.append(turn("assistant", "Done."))
        desk.set("idle")
        done = await call(c, "wait_for_session")
    assert running["status"] == "still_working"
    assert noted["status"] == "still_working"
    assert done["status"] == "idle"
    assert done["reply"] == "Done."


async def test_a_followed_session_is_named_whatever_its_capitals(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="BILLING-AB", message="Run the export.")
        desk.transcript.append(turn("assistant", "Export done."))
        out = await call(c, "list_active_sessions")
        stopped = await call(c, "follow_session", session="Billing-Ab", follow=False)
    assert out["news"] == "[Juniper] billing-ab (idle): reply to you, read with after=1"
    assert stopped["status"] == "not_followed"


async def test_reading_progress_mid_turn_then_a_restart_keeps_the_session_followed(tmp_path, world):
    root, live = world
    desk = Desk(live, str(root / "billing"))
    async with bridge(tmp_path, desk, Store(tmp_path / "bridge.db")) as url:
        http, client = conversation(url)
        async with http, client as c:
            sent = await call(c, "message_active_session", session="billing-ab", message="Run the export.")
            desk.set("busy")
            desk.transcript.append(turn("assistant", "Exporting the first table."))
            desk.transcript.append(turn("assistant", "Exporting the second table."))
            read = await call(c, "wait_for_session", session="billing-ab", after=sent["next_after"], wait_seconds=0.1)
            mid = await call(c, "list_active_sessions")
    desk.transcript.append(turn("assistant", "Export done."))
    desk.set("idle")
    async with bridge(tmp_path, desk, Store(tmp_path / "bridge.db")) as url:
        http, client = conversation(url)
        async with http, client as c:
            out = await call(c, "list_active_sessions")
            waited = await call(c, "wait_for_session", wait_seconds=0.1)
    assert read["status"] == "still_working"
    assert mid["news"] == "[Juniper] billing-ab (busy): nothing new"
    assert out["news"].startswith("[Juniper] billing-ab (idle): reply to you")
    assert waited["reply"] == "Export done."


async def test_a_send_after_every_reply_is_read_waits_for_its_own_reply(served):
    # Mid-turn, the relay has read the latest progress line and sends again: the next finished
    # turn answers this message, and reading progress before it doesn't end the following.
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Run the export.")
        desk.set("busy")
        desk.transcript.append(turn("assistant", "Exporting the first table."))
        await call(c, "read_session_output", session="billing-ab")
        await call(c, "message_active_session", session="billing-ab", message="Also the archive.")
        desk.transcript.append(turn("assistant", "Exporting the archive."))
        read = await call(c, "read_session_output", session="billing-ab")
        mid = await call(c, "list_active_sessions")
        desk.transcript.append(turn("assistant", "Both done."))
        desk.set("idle")
        out = await call(c, "list_active_sessions")
    assert read["turns"][-1]["text"] == "Exporting the archive."
    assert mid["news"] == "[Juniper] billing-ab (busy): nothing new"
    assert out["news"].startswith("[Juniper] billing-ab (idle): reply to you")


# Relay handles: the owner often has two or more voice conversations open at once, each a small
# relay, and all of them reach the bridge over one connection; so each relay passes a handle on the
# calls that follow or read, which the bridge hands out, and each handle has its own follows and
# news (owner, 2026-11-12). A Claude Code session is never asked: its handle is its own session name.


async def test_a_call_that_follows_without_a_handle_gets_a_new_one(served):
    # It goes ahead: a refusal came back as an error, which the Claude app tells its assistant to
    # report to the owner as a failure (seen live, 2026-11-12).
    url, desk = served
    desk.set("busy")
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Run the export.", handle="Rowan")
        results = [
            await call(c, "message_active_session", session="billing-ab", message="Go.", handle=None),
            await call(c, "follow_session", session="billing-ab", handle=None),
        ]
        handed = [r["handle"] for r in results]
        out = await call(c, "list_active_sessions", handle=handed[0])
    for res, handle in zip(results, handed, strict=True):
        assert res["next_step"].startswith(f'Your handle is "{handle}"'), res["next_step"]
        assert "Rowan (follows billing-ab)" in res["next_step"]  # the handles in use, to take one back
    assert results[0]["status"] == "delivered" and len(desk.transcript) == 4  # both messages were sent
    assert results[1]["status"] == "following"
    assert out["news"] == f"[{handed[0]}] billing-ab (busy): nothing new"


async def test_a_read_without_a_handle_works_and_hands_out_none(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        out = await call(c, "read_session_output", session="billing-ab", handle=None)
        waited = await call(c, "wait_for_session", handle=None, wait_seconds=60)
        later = await call(c, "list_folders")
        listed = await call(c, "list_active_sessions")
    assert out["turns"][-1]["text"] == "earlier answer"
    assert out["next_step"].startswith("No handle passed") and "handle" not in out
    assert waited["status"] == "nothing_to_wait_for" and "handle" not in waited
    assert "next_step" not in later  # only the tools that take a handle say it
    assert [h["handle"] for h in listed["handles"]] == ["Juniper", "Rowan", "@kQzw"]  # none added


async def test_each_handle_follows_its_own_sessions(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Run the export.", handle="Rowan")
        desk.transcript.append(turn("assistant", "Export done."))
        mine = await call(c, "list_active_sessions", handle="Rowan")
        other = await call(c, "list_active_sessions", handle="Juniper")
        waited = await call(c, "wait_for_session", handle="Juniper", wait_seconds=60)
    assert mine["news"] == "[Rowan] billing-ab (idle): reply to you, read with after=1"
    assert "news" not in other and "next_step" not in other
    assert waited["status"] == "nothing_to_wait_for"


async def test_a_handle_is_matched_whatever_its_capitals(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Run the export.", handle="Rowan")
        desk.transcript.append(turn("assistant", "Export done."))
        out = await call(c, "list_active_sessions", handle=" rowan ")
    assert out["news"] == "[Rowan] billing-ab (idle): reply to you, read with after=1"


async def test_a_permission_prompt_shows_for_every_handle_and_without_one(served):
    url, desk = served
    desk.set("waiting", waiting_for="permission prompt")
    http, client = conversation(url)
    async with http, client as c:
        named = await call(c, "list_active_sessions", handle="Rowan")
        nameless = await call(c, "list_folders")
    assert named["news"] == "[Rowan] billing-ab: waiting on a permission prompt"
    assert nameless["news"] == "billing-ab: waiting on a permission prompt"


async def test_list_active_sessions_lists_the_handles_in_use(served):
    url, desk = served
    desk.set("busy")
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Run the export.", handle="Rowan")
        await call(c, "read_session_output", session="billing-ab", handle="Juniper")
        out = await call(c, "list_active_sessions", handle="Juniper")
    assert {"handle": "Rowan", "follows": ["billing-ab"]} in out["handles"]
    assert {"handle": "Juniper", "follows": []} in out["handles"]


async def test_the_shared_list_becomes_the_unclaimed_handle_voice_relay(tmp_path, world):
    # What the relays shared before handles is kept for whoever carries on that work.
    root, live = world
    desk = Desk(live, str(root / "billing"))
    store = Store(tmp_path / "bridge.db")
    store.set_setting("read_marks", {"sid-1": {"seen": 1, "sent": 1, "replied": False, "name": "billing-ab"}})
    desk.transcript.append(turn("assistant", "Export done."))
    async with bridge(tmp_path, desk, store) as url:
        http, client = conversation(url)
        async with http, client as c:
            read = await call(c, "read_session_output", session="billing-ab", handle=None, after=1)
            out = await call(c, "list_active_sessions", handle="voice relay")
    assert "voice relay (follows billing-ab)" in read["next_step"]
    assert out["news"] == "[voice relay] billing-ab (idle): reply to you, read with after=1"


async def test_a_handle_following_nothing_unused_for_a_day_is_dropped(tmp_path, world):
    root, live = world
    desk = Desk(live, str(root / "billing"))
    store = Store(tmp_path / "bridge.db")
    old = time.time() - 25 * 3600
    store.set_setting(
        "relay_handles",
        {
            "Rowan": {"used": old, "sessions": {"sid-1": {"seen": 1, "sent": None, "replied": False}}},
            "Ash": {"used": old, "sessions": {"sid-1": {"seen": 1, "sent": 1, "replied": False, "name": "billing-ab"}}},
        },
    )
    async with bridge(tmp_path, desk, store) as url:
        http, client = conversation(url)
        async with http, client as c:
            out = await call(c, "list_active_sessions", handle="Ash")
    assert [h["handle"] for h in out["handles"]] == ["Ash"]  # Ash still follows a session


async def test_a_claude_code_session_s_handle_is_its_own_name(tmp_path, world):
    # A call from Claude Code carries its tool-call id, which its transcript records: the bridge
    # knows the session and never asks it for a handle, and what it follows stays its own.
    root, live = world
    desk = Desk(live, str(root / "billing"))
    planner = Desk(live, str(root / "billing"), name="planner", sid="sid-cc", pid=os.getppid())
    record = tmp_path / "projects" / project_key_for_directory(planner.cwd) / "sid-cc.jsonl"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"type": "assistant", "id": "toolu_planner_1"}) + "\n", encoding="utf-8")
    from_planner = {"claudecode/toolUseId": "toolu_planner_1"}
    async with bridge(tmp_path, desk, Store(tmp_path / "bridge.db"), planner) as url:
        http, client = conversation(url)
        async with http, client as c:
            sent = await c.call_tool(
                "message_active_session", {"session": "billing-ab", "message": "Go."}, meta=from_planner
            )
            desk.transcript.append(turn("assistant", "Gone."))
            voice = await call(c, "list_active_sessions")
            own = await c.call_tool("list_folders", {}, meta=from_planner)
    assert not sent.is_error, sent.content
    assert "news" not in voice  # not the voice relay's
    assert own.structured_content["news"] == "[planner] billing-ab (idle): reply to you, read with after=1"


async def test_a_send_while_a_reply_is_unread_is_followed_once_that_reply_is_read(served):
    # The relay sends again before reading the reply to its earlier message, while the session
    # answers another session: reading up to that answer reads the earlier reply, and the follow
    # moves on to the later send instead of ending.
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Run the export.")
        desk.transcript.append(turn("assistant", "Export done."))
        desk.transcript.append(turn("user", "Another session: which folder?", "peer"))
        desk.set("busy")
        desk.transcript.append(turn("assistant", "In billing."))
        sent = await call(c, "message_active_session", session="billing-ab", message="Now the archive.")
        read = await call(c, "read_session_output", session="billing-ab", after=1)
        desk.transcript.append(turn("assistant", "Archive done."))
        desk.set("idle")
        out = await call(c, "list_active_sessions")
    assert "In billing." in [t["text"] for t in read["turns"]]
    assert read["news"] == "[Juniper] billing-ab (busy): nothing new"
    assert "Nothing left to follow" not in read.get("next_step", "")
    assert out["news"].startswith("[Juniper] billing-ab (idle): reply to you")
    assert sent["status"] == "delivered"


async def test_a_handle_looks_like_a_code_not_a_name(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        sent = [
            await call(c, "message_active_session", session="billing-ab", message="Go.", handle=None)
            for _ in range(100)
        ]
    for out in sent:
        handle = out["handle"]
        assert re.fullmatch(r"@[A-Za-z]{4}", handle), handle
        assert handle != handle.lower() and handle != handle.upper(), handle


async def test_the_news_line_starts_with_the_handle(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Run the export.", handle="@kQzw")
        out = await call(c, "list_active_sessions", handle="@kQzw")
    assert out["news"] == "[@kQzw] billing-ab (idle): nothing new"


async def test_a_handle_not_in_use_counts_as_none(served):
    # The bridge hands handles out, so one it doesn't know is a typo or one long dropped: taking it
    # quietly would start an empty list, and the relay would hear "nothing new" while its replies
    # waited under the right one (owner, 2026-11-12).
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Run the export.", handle="Rowan")
        sent = await call(c, "message_active_session", session="billing-ab", message="Go.", handle="Rowen")
        read = await call(c, "read_session_output", session="billing-ab", handle="Rowen")
        out = await call(c, "list_active_sessions", handle="Rowan")
    step = sent["next_step"]
    assert step.startswith('Handle "Rowen" isn\'t one in use (a typo, or dropped after a day following nothing)')
    assert f'Your handle is "{sent["handle"]}"' in step and "this call's follow moves into it" in step
    assert "Rowan (follows billing-ab)" in step
    assert read["next_step"].startswith('Handle "Rowen" isn\'t one in use') and "handle" not in read
    assert "Rowen" not in [h["handle"] for h in out["handles"]]


async def test_handles_handed_out_at_the_same_moment_merge_on_first_use(tmp_path, world):
    # A relay's first calls, made at once without a handle, each get one; the relay keeps one, and
    # the replies under the others would be missed (owner, 2026-11-12).
    root, live = world
    desk = Desk(live, str(root / "billing"))
    other = Desk(live, str(root / "billing"), name="ledger", sid="sid-2", pid=os.getppid())
    async with bridge(tmp_path, desk, Store(tmp_path / "bridge.db"), other) as url:
        http, client = conversation(url)
        async with http, client as c:
            first, second = await asyncio.gather(
                call(c, "message_active_session", session="billing-ab", message="Run the export.", handle=None),
                call(c, "message_active_session", session="ledger", message="Close the month.", handle=None),
            )
            other.transcript.append(turn("assistant", "Month closed."))
            out = await call(c, "list_active_sessions", handle=first["handle"])
    assert first["handle"] != second["handle"]
    assert (
        out["news"]
        == f"[{first['handle']}] billing-ab (idle): nothing new | ledger (idle): reply to you, read with after=1"
    )
    assert out["next_step"].startswith("Also following ledger, from your calls a moment ago without your handle")
    assert second["handle"] not in [h["handle"] for h in out["handles"]]


# A read came back cut: the relay's own long message took most of the page, and the reply was left
# for another read, which the relay skipped before telling the owner (seen live, 2026-11-12). So a
# relayed message shows as one line, a finished turn's reply comes back whole whatever the page,
# the same way from a read as from a wait, and a reply not yet whole says to read the rest (owner).


async def test_a_relayed_message_shows_in_a_read_as_one_line(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        sent = await call(c, "message_active_session", session="billing-ab", message="Run the export. " + "x" * 2900)
        desk.transcript.append(turn("assistant", "Export done."))
        out = await call(c, "read_session_output", session="billing-ab", after=sent["next_after"])
    mine = out["turns"][0]
    assert mine["role"] == "user" and mine["text"].startswith("[relayed message] Run the export. xxx")
    assert len(mine["text"]) < 120
    assert out["turns"][1]["text"] == "Export done." and out["turns_left"] == 0


def long_turn(desk, progress=12):
    """A turn of many progress lines, more than a page holds, ending in a long report."""
    desk.transcript += [turn("assistant", f"Step {i}: " + "p" * 300) for i in range(progress)]
    report = "Report: " + "r" * 5000
    desk.transcript.append(turn("assistant", report))
    return report


async def test_a_finished_turn_s_reply_comes_back_whole_in_a_read_past_the_page(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        sent = await call(c, "message_active_session", session="billing-ab", message="Run the export.")
        report = long_turn(desk)
        out = await call(c, "read_session_output", session="billing-ab", after=sent["next_after"])
        later = await call(c, "list_active_sessions")
    assert out["reply"] == report and out["reply_final"] is True
    assert out["turns_left"] > 0  # the progress lines still page as before
    assert all(t["text"].startswith("Step") for t in out["turns"][1:])
    assert "next_step" not in later and "news" not in later  # the reply is read: following it is over


async def test_a_read_and_a_wait_give_a_finished_turn_s_reply_the_same_way(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        sent = await call(c, "message_active_session", session="billing-ab", message="Run the export.")
        long_turn(desk)
        read = await call(c, "read_session_output", session="billing-ab", after=sent["next_after"])
        waited = await call(c, "wait_for_session", session="billing-ab", after=sent["next_after"])
    for key in ("reply", "reply_final", "reply_index"):
        assert read[key] == waited[key], key


async def test_a_reply_not_yet_whole_says_to_read_the_rest(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        sent = await call(c, "message_active_session", session="billing-ab", message="Run the export.")
        desk.set("busy")
        desk.transcript += [turn("assistant", f"Step {i}: " + "p" * 300) for i in range(12)]
        out = await call(c, "read_session_output", session="billing-ab", after=sent["next_after"])
    assert out["turns_left"] > 0 and out["reply_final"] is False
    assert out["next_step"].startswith(
        f"You don't have the whole reply yet: read_session_output(after={out['next_after']}) before telling the owner"
    ), out["next_step"]


async def test_a_stray_handle_folds_into_the_next_handle_passed_that_already_had_its_sessions(served):
    # Seen as a risk (owner, 2026-11-13): a relay that forgets its handle on a send gets a new one,
    # goes back to its own, and the reply under the new one would go unheard.
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "read_session_output", session="billing-ab", handle="Rowan")
        desk.set("busy")
        stray = await call(c, "message_active_session", session="billing-ab", message="Go.", handle=None)
        desk.transcript.append(turn("assistant", "Gone."))
        desk.set("idle")
        out = await call(c, "list_active_sessions", handle="Rowan")
    assert out["news"].startswith("[Rowan] billing-ab (idle): reply to you")
    assert out["next_step"].startswith("Also following billing-ab, from your calls a moment ago without your handle")
    assert stray["handle"] not in [h["handle"] for h in out["handles"]]


async def test_a_new_relay_s_handle_is_not_folded_into_another_relay_s(served):
    # Another relay's waits call every half minute: a new relay's first handle, on a session that
    # relay never touched, stays the new relay's.
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        new = await call(c, "message_active_session", session="billing-ab", message="Go.", handle=None)
        out = await call(c, "list_active_sessions", handle="Rowan")
    assert new["handle"] in [h["handle"] for h in out["handles"]]
    assert "Also following" not in out.get("next_step", "")


# Two levels of following (owner, 2026-11-13): a session that owes the relay a reply is followed
# as before; once that reply is read, it stays in a quiet follow, shown only when it has a new
# finished turn the relay hasn't read, with who started it, so a turn from the owner's typing or
# another session isn't missed. Quiet ones that stay quiet never show, which kept the old list of
# every session touched from turning into noise.


async def test_a_session_stays_in_a_quiet_follow_after_its_reply_is_read(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        sent = await call(c, "message_active_session", session="billing-ab", message="Run the export.")
        desk.transcript.append(turn("assistant", "Export done."))
        await call(c, "read_session_output", session="billing-ab", after=sent["next_after"])
        quiet = await call(c, "list_active_sessions")
        desk.transcript.append(turn("user", "Now the invoices.", "human"))
        desk.transcript.append(turn("assistant", "Invoices done."))
        typed = await call(c, "list_active_sessions")
        await call(c, "read_session_output", session="billing-ab", after=3)
        desk.transcript.append(turn("user", "From another session: which folder?", "peer"))
        desk.transcript.append(turn("assistant", "In billing."))
        peer = await call(c, "list_active_sessions")
    assert "news" not in quiet
    assert typed["news"] == "[Juniper] billing-ab (idle): new turn, started by the owner typing, read with after=3"
    assert peer["news"] == "[Juniper] billing-ab (idle): new turn, started by another session, read with after=5"


async def test_a_new_turn_in_a_quiet_follow_wakes_a_wait(tmp_path, world):
    root, live = world
    desk = Desk(live, str(root / "billing"))
    other = Desk(live, str(root / "billing"), name="ledger", sid="sid-2", pid=os.getppid())
    async with bridge(tmp_path, desk, Store(tmp_path / "bridge.db"), other) as url:
        http, client = conversation(url)
        async with http, client as c:
            sent = await call(c, "message_active_session", session="billing-ab", message="Run the export.")
            desk.transcript.append(turn("assistant", "Export done."))
            await call(c, "read_session_output", session="billing-ab", after=sent["next_after"])
            await call(c, "message_active_session", session="ledger", message="Close the month.")
            other.set("busy")
            desk.transcript.append(turn("user", "Now the invoices.", "human"))
            desk.transcript.append(turn("assistant", "Invoices done."))
            out = await call(c, "wait_for_session")
    assert out["session"] == "billing-ab" and out["reply"] == "Invoices done."


async def test_a_quiet_follow_can_be_stopped(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        sent = await call(c, "message_active_session", session="billing-ab", message="Run the export.")
        desk.transcript.append(turn("assistant", "Export done."))
        await call(c, "read_session_output", session="billing-ab", after=sent["next_after"])
        stopped = await call(c, "follow_session", session="billing-ab", follow=False)
        desk.transcript.append(turn("user", "Now the invoices.", "human"))
        desk.transcript.append(turn("assistant", "Invoices done."))
        out = await call(c, "list_active_sessions")
    assert stopped["status"] == "not_followed"
    assert "news" not in out


async def test_a_quiet_follow_ends_after_a_day_with_nothing_new(tmp_path, world):
    root, live = world
    desk = Desk(live, str(root / "billing"))
    store = Store(tmp_path / "bridge.db")
    old = time.time() - 25 * 3600
    mark = {"seen": 1, "sent": None, "replied": False, "name": "billing-ab", "quiet": old}
    store.set_setting("relay_handles", {"Juniper": {"used": time.time(), "sessions": {"sid-1": mark}}})
    async with bridge(tmp_path, desk, store) as url:
        http, client = conversation(url)
        async with http, client as c:
            await call(c, "list_active_sessions")
            desk.transcript.append(turn("user", "Now the invoices.", "human"))
            desk.transcript.append(turn("assistant", "Invoices done."))
            out = await call(c, "list_active_sessions")
    assert "news" not in out
