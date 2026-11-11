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
async def bridge(tmp_path, desk, store, *others):
    """The bridge over HTTP, as the Claude app reaches it, for one desk session or more."""
    root = Path(desk.cwd).parent
    desks = {d.sid: d for d in (desk, *others)}

    async def deliver(name, text):
        # The relayed message lands in the session's conversation, marked as from another session.
        next(d for d in desks.values() if d.name == name).transcript.append(turn("user", text, "peer"))
        return {"delivered": True, "detail": "DELIVERED"}

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


async def call(client, name, **args):
    res = await client.call_tool(name, args)
    assert not res.is_error, res.content
    return res.structured_content


async def test_following_a_session_ends_once_its_finished_turn_is_read(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        sent = await call(c, "message_active_session", session="billing-ab", message="Run the export.")
        desk.transcript.append(turn("assistant", "Export done."))
        later = await call(c, "list_folders")
        read = await call(c, "read_session_output", session="billing-ab", after=sent["next_after"])
        after = await call(c, "list_folders")
    assert later["news"] == "billing-ab (idle): reply to you, read with after=1"
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
        out = await call(c, "list_folders")
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
        later = await call(c, "list_folders")
    assert sent["next_after"] == -1
    assert later["news"] == "billing-ab (idle): reply to you, read with after=-1"


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
        working = await call(c, "list_folders")
        desk.transcript.append(turn("assistant", "Export done."))
        desk.set("idle")
        done = await call(c, "list_folders")
    assert working["news"] == "billing-ab (busy): nothing new"
    assert done["news"] == "billing-ab (idle): reply to you, read with after=1"


async def test_a_question_still_unanswered_when_ask_returns_is_a_reply_later(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        asked = await call(c, "ask_active_session", session="billing-ab", message="Is the export done?")
        desk.transcript.append(turn("assistant", "Yes."))
        out = await call(c, "list_folders")
    assert asked["status"] == "still_working"
    assert out["news"] == f"billing-ab (idle): reply to you, read with after={asked['next_after']}"


async def test_a_send_to_a_session_the_owner_typed_in_since_the_last_read_says_so(served):
    # Not news on every result: it matters when the relay writes to that session again.
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        sent = await call(c, "message_active_session", session="billing-ab", message="Check the script.")
        desk.transcript.append(turn("assistant", "Checked."))
        await call(c, "read_session_output", session="billing-ab", after=sent["next_after"])
        desk.transcript.append(turn("user", "I ran it myself.", "human"))  # on the phone, directly
        desk.transcript.append(turn("assistant", "Then it is done."))
        quiet = await call(c, "list_folders")
        again = await call(c, "message_active_session", session="billing-ab", message="And the invoices?")
    assert "news" not in quiet
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
        out = await call(c, "list_folders")
    assert out["news"] == "billing-ab: waiting on a permission prompt"


async def test_a_followed_session_that_ended_says_so_once_then_leaves_the_line(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Wrap up.")
        desk.gone()
        ended = await call(c, "list_folders")
        later = await call(c, "list_folders")
    assert ended["news"] == "billing-ab: ended"  # it can't be read any more
    assert "news" not in later


async def test_a_session_that_ended_before_its_reply_was_read_says_so(served):
    # It can't be read any more; the relay can still resume it with attach_conversation.
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Wrap up.")
        desk.transcript.append(turn("assistant", "Wrapped up."))
        desk.gone()
        out = await call(c, "list_folders")
    assert out["news"] == "billing-ab: ended before you read its reply"


async def test_two_sessions_are_set_apart_in_one_line(served):
    url, desk = served
    other = Desk(desk.live, desk.cwd, name="ledger, cut-over", sid="sid-2")
    other.set("waiting", waiting_for="permission prompt")
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Go.")
        desk.transcript.append(turn("assistant", "Gone."))
        out = await call(c, "list_folders")
    assert (
        out["news"]
        == "billing-ab (idle): reply to you, read with after=1 | ledger, cut-over: waiting on a permission prompt"
    )


async def test_a_session_handed_over_is_followed_until_any_turn_it_finishes(served):
    # "Tell me when it's done": the owner hands over a session they are driving themselves, so a
    # turn answering their own typing counts.
    url, desk = served
    desk.set("busy")
    http, client = conversation(url)
    async with http, client as c:
        handed = await call(c, "follow_session", session="billing-ab")
        working = await call(c, "list_folders")
        desk.transcript.append(turn("user", "Start on the invoices.", "human"))
        desk.transcript.append(turn("assistant", "Invoices done."))
        desk.set("idle")
        done = await call(c, "list_folders")
    assert handed["status"] == "following"
    assert working["news"] == "billing-ab (busy): nothing new"  # what came before isn't news
    assert done["news"] == "billing-ab (idle): reply to you, read with after=1"


async def test_a_session_handed_over_at_rest_is_already_done_not_followed(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        handed = await call(c, "follow_session", session="billing-ab")
        out = await call(c, "list_folders")
    assert handed["status"] == "already_done"
    assert "news" not in out


async def test_following_can_be_stopped(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Run the export.")
        stopped = await call(c, "follow_session", session="billing-ab", follow=False)
        desk.transcript.append(turn("assistant", "Export done."))
        out = await call(c, "list_folders")
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
            out = await call(c, "list_folders")
    assert out["news"] == "billing-ab (idle): reply to you, read with after=1"


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
            out = await call(c, "list_folders")
    assert out["news"] == "billing-ab (idle): reply to you, read with after=1"


async def test_nothing_followed_and_no_prompt_means_no_news_field(served):
    url, _ = served
    http, client = conversation(url)
    async with http, client as c:
        out = await call(c, "list_folders")
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
        later = await call(c, "list_folders")
    assert again["next_after"] == 3  # the report is turn 3, before the new message
    news = f"billing-ab (busy): reply to you, read with after={first['next_after']}"
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
        done = await call(c, "list_folders")
    assert again["news"] == "billing-ab (busy): nothing new"
    assert done["news"] == f"billing-ab (idle): reply to you, read with after={again['next_after']}"


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
        out = await call(c, "list_folders")
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
        out = await call(c, "list_folders")
    assert out["next_step"].startswith("billing-ab has finished its turn: read it")


async def test_a_session_paused_on_the_owner_is_told_once_not_waited_on(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Delete the old exports.")
        desk.set("waiting", waiting_for="permission prompt")
        first = await call(c, "list_folders")
        second = await call(c, "list_folders")
    assert first["next_step"].startswith("billing-ab is paused on a permission prompt only the owner can answer")
    assert "next_step" not in second
    assert second["news"] == "billing-ab: waiting on a permission prompt"  # still named until answered


async def test_the_result_where_following_ends_says_there_is_nothing_left_to_follow(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        sent = await call(c, "message_active_session", session="billing-ab", message="Run the export.")
        desk.transcript.append(turn("assistant", "Export done."))
        await call(c, "list_folders")
        read = await call(c, "read_session_output", session="billing-ab", after=sent["next_after"])
        later = await call(c, "list_folders")
    assert read["next_step"] == "Nothing left to follow: every reply is read. No need to tell the owner."
    assert "next_step" not in later


async def test_nothing_followed_means_no_next_step(served):
    url, _ = served
    http, client = conversation(url)
    async with http, client as c:
        out = await call(c, "list_folders")
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
            later = await call(c, "list_folders")
    assert out["session"] == "ledger" and out["reply"] == "Month closed." and out["reply_final"] is True
    assert later["news"] == "billing-ab (busy): nothing new"  # ledger's reply was read: no longer followed


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
    return turn("user", f"<task-notification>\n<task-id>{task_id}</task-id>\n</task-notification>", "task-notification")


async def test_a_session_with_a_background_task_still_running_is_not_at_rest(served):
    # Its turn has ended, but the task's completion note will start another: more is to come.
    url, desk = served
    desk.transcript += [started_in_background("bcw"), turn("assistant", "I'll say done when it finishes.")]
    http, client = conversation(url)
    async with http, client as c:
        handed = await call(c, "follow_session", session="billing-ab")
        out = await call(c, "list_folders")
    assert handed["status"] == "following"
    assert out["news"] == "billing-ab (turn ended, background task still running): nothing new"
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
        out = await call(c, "list_folders")
        stopped = await call(c, "follow_session", session="Billing-Ab", follow=False)
    assert out["news"] == "billing-ab (idle): reply to you, read with after=1"
    assert stopped["status"] == "not_followed"
