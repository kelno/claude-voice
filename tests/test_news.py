"""A news line on every result: what the relay hasn't read yet, without a call of its own.

With several sessions running, a relay misses replies, most of all when the owner talks to a
session directly. Each conversation (one MCP session in the Claude app) keeps a read marker per
session it started, resumed, messaged or read; a result then names the sessions with turns past
that marker, and any session waiting on a permission prompt, which only the owner can answer.
"""

import asyncio
import json
import os
from types import SimpleNamespace

import pytest
import uvicorn
from fakes import FakeClaude
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from test_server import TOKEN, free_port

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

    def __init__(self, live, cwd, name="billing-ab", sid="sid-1"):
        self.live, self.cwd, self.name, self.sid = live, cwd, name, sid
        self.transcript = [turn("user", "earlier question", "human"), turn("assistant", "earlier answer")]
        self.set("idle")

    def set(self, status, waiting_for=None):
        entry = {"pid": PID, "sessionId": self.sid, "cwd": self.cwd, "name": self.name, "status": status}
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


@pytest.fixture
async def served(tmp_path, world):
    """The bridge over HTTP, as the Claude app reaches it, with one desk session."""
    root, live = world
    desk = Desk(live, str(root / "billing"))
    sent = []

    async def deliver(name, text):
        sent.append(text)
        # The relayed message lands in the session's conversation, marked as from another session.
        desk.transcript.append(turn("user", text, "peer"))
        return {"delivered": True, "detail": "DELIVERED"}

    port = free_port()
    store = Store(tmp_path / "bridge.db")
    oauth = OAuthProvider(store, TOKEN, f"http://127.0.0.1:{port}", static_token=TOKEN)
    m = SessionManager(store, project_root=root, client_factory=FakeClaude())
    server = build_server(
        m,
        oauth=oauth,
        conversations=lambda **k: [],
        live_dir=live,
        deliver=deliver,
        read_transcript=lambda sid, directory: list(desk.transcript),
        poll_seconds=0.01,
        projects_dir=tmp_path / "projects",
        max_wait_seconds=0.3,
    )
    uv = uvicorn.Server(uvicorn.Config(build_app(server), port=port, log_level="warning"))
    task = asyncio.create_task(uv.serve())
    while not uv.started:  # noqa: ASYNC110 - uvicorn exposes no event
        await asyncio.sleep(0.01)
    yield f"http://127.0.0.1:{port}", desk
    uv.should_exit = True
    await task


def conversation(url):
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {TOKEN}"})
    return http, Client(streamable_http_client(f"{url}/mcp", http_client=http), mode="legacy")


async def call(client, name, **args):
    res = await client.call_tool(name, args)
    assert not res.is_error, res.content
    return res.structured_content


async def test_a_reply_from_a_messaged_session_is_news_until_it_is_read(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        sent = await call(c, "message_active_session", session="billing-ab", message="Run the export.")
        assert "news" not in sent
        desk.transcript.append(turn("assistant", "Export done."))
        later = await call(c, "list_projects")
        read = await call(c, "read_session_output", session="billing-ab", after=sent["next_after"])
        after = await call(c, "list_projects")
    assert later["news"] == "billing-ab (idle): 1 unread message, read with after=1"
    assert list(later)[-1] != "news" and "news" in list(later)[:3]  # given before the result itself
    assert "news" not in read and "news" not in after


async def test_a_turn_the_owner_typed_to_the_session_is_news_and_says_so(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        sent = await call(c, "message_active_session", session="billing-ab", message="Check the script.")
        await call(c, "read_session_output", session="billing-ab", after=sent["next_after"])
        desk.transcript.append(turn("user", "I ran it myself.", "human"))  # on the phone, directly
        desk.transcript.append(turn("assistant", "Then it is done."))
        desk.set("busy")
        out = await call(c, "list_projects")
    assert out["news"] == "billing-ab (busy): 2 unread messages, 1 typed by the owner, read with after=2"


async def test_another_conversation_does_not_get_this_ones_news(served):
    url, desk = served
    http, client = conversation(url)
    other_http, other = conversation(url)
    async with http, client as c, other_http, other as o:
        await call(c, "message_active_session", session="billing-ab", message="Go.")
        desk.transcript.append(turn("assistant", "Gone."))
        mine = await call(c, "list_projects")
        theirs = await call(o, "list_projects")
    assert "billing-ab" in mine["news"]
    assert "news" not in theirs


async def test_a_session_waiting_on_a_permission_prompt_is_news_to_every_conversation(served):
    url, desk = served
    desk.set("waiting", waiting_for="permission prompt")
    http, client = conversation(url)
    async with http, client as c:
        out = await call(c, "list_projects")
    assert out["news"] == "billing-ab (waiting on a permission prompt)"


async def test_a_followed_session_that_ended_with_unread_turns_says_it_ended(served):
    url, desk = served
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Wrap up.")
        desk.transcript.append(turn("assistant", "Wrapped up."))
        desk.gone()
        out = await call(c, "list_projects")
    assert out["news"] == "billing-ab (ended): 1 unread message"  # it can't be read any more


async def test_two_sessions_are_set_apart_in_one_line(served):
    url, desk = served
    other = Desk(desk.live, desk.cwd, name="ledger, cut-over", sid="sid-2")
    other.set("waiting", waiting_for="permission prompt")
    http, client = conversation(url)
    async with http, client as c:
        await call(c, "message_active_session", session="billing-ab", message="Go.")
        desk.transcript.append(turn("assistant", "Gone."))
        out = await call(c, "list_projects")
    assert (
        out["news"]
        == "billing-ab (idle): 1 unread message, read with after=1 | ledger, cut-over (waiting on a permission prompt)"
    )


async def test_nothing_new_means_no_news_field(served):
    url, _ = served
    http, client = conversation(url)
    async with http, client as c:
        out = await call(c, "list_projects")
    assert "news" not in out
