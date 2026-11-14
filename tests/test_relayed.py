"""What a session is told about a message the relay sends it: who sees what, and where to answer."""

import json
import os

import pytest
import test_news as news
from mcp.client import Client
from test_ask import Target, bridge, call
from test_news import world  # noqa: F401  # the fixture the harness below needs

from claude_voice.server import RELAYED_MESSAGE_NOTE
from claude_voice.store import Store
from claude_voice.transcripts import project_key_for_directory


@pytest.fixture
def root(tmp_path):
    (tmp_path / "src" / "billing").mkdir(parents=True)
    return tmp_path / "src"


@pytest.fixture
def live(tmp_path):
    d = tmp_path / "live"
    d.mkdir()
    return d


def test_the_note_says_who_sees_what_and_where_to_answer():
    note = RELAYED_MESSAGE_NOTE
    assert "relay" in note
    assert "owner" in note and "see" in note
    assert "self-contained" in note
    assert "SendMessage" in RELAYED_MESSAGE_NOTE and "inbox" in RELAYED_MESSAGE_NOTE
    # A relayed approval isn't the owner's; the note says where to get the owner's own.
    assert "confirm_ask" in RELAYED_MESSAGE_NOTE


async def test_a_relayed_message_carries_the_note_after_its_own_words(tmp_path, root, live):
    target = Target(live, str(root / "billing"))
    sent = []

    async def deliver(name, text):
        sent.append(text)
        return {"delivered": True, "detail": "DELIVERED"}

    async with Client(bridge(tmp_path, root, live, target, deliver)) as c:
        await call(c, "message_active_session", session="billing-ab", message="Is the export fixed?")
        await call(c, "ask_active_session", session="billing-ab", message="And the import?", wait_seconds=0)
    assert sent[0].startswith("Is the export fixed?") and RELAYED_MESSAGE_NOTE in sent[0]
    assert sent[1].startswith("And the import?") and RELAYED_MESSAGE_NOTE in sent[1]


async def from_a_claude_code_session(tmp_path, world, *calls):  # noqa: F811
    """What billing-ab got from each call, made by the Claude Code session "planner" on this PC
    (its call carries its tool-call id, which its transcript records) or, with meta None, by the
    claude.ai relay."""
    root, live = world
    desk = news.Desk(live, str(root / "billing"))
    planner = news.Desk(live, str(root / "billing"), name="planner", sid="sid-cc", pid=os.getppid())
    record = tmp_path / "projects" / project_key_for_directory(planner.cwd) / "sid-cc.jsonl"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"type": "assistant", "id": "toolu_planner_1"}) + "\n", encoding="utf-8")
    got = []
    async with news.bridge(tmp_path, desk, Store(tmp_path / "bridge.db"), planner) as url:
        http, client = news.conversation(url)
        async with http, client as c:
            for tool, args, meta in calls:
                out = await c.call_tool(tool, {**args, "handle": news.HANDLE} if meta is None else args, meta=meta)
                assert not out.is_error, out.content
                got.append(desk.transcript[-1].message["content"])
    return got


PLANNER = {"claudecode/toolUseId": "toolu_planner_1"}


async def test_a_message_from_a_claude_code_session_says_to_answer_it_by_direct_message(tmp_path, world):  # noqa: F811
    # A Claude Code session has an inbox: a direct message wakes it, while a reply in the text is
    # seen only when it next reads the session (owner, 2026-11-14; ledger F38).
    by_planner, by_voice = await from_a_claude_code_session(
        tmp_path,
        world,
        ("message_active_session", {"session": "billing-ab", "message": "Go."}, PLANNER),
        ("message_active_session", {"session": "billing-ab", "message": "And you?"}, None),
    )
    assert by_planner.startswith("Go.\n\n[From the claude-voice bridge: this message comes from planner,")
    assert 'SendMessage to "planner"' in by_planner and "confirm_ask" in by_planner
    assert "inbox" not in by_planner and RELAYED_MESSAGE_NOTE not in by_planner
    # The claude.ai relay has no inbox: its note is unchanged.
    assert by_voice == f"And you?\n\n{RELAYED_MESSAGE_NOTE}"


async def test_a_question_from_a_claude_code_session_is_answered_in_the_reply_text(tmp_path, world):  # noqa: F811
    # ask_active_session waits for the reply in the session's text, so a direct message would miss it.
    (asked,) = await from_a_claude_code_session(
        tmp_path,
        world,
        ("ask_active_session", {"session": "billing-ab", "message": "Done?", "wait_seconds": 0}, PLANNER),
    )
    assert asked.startswith("Done?\n\n[From the claude-voice bridge: this message comes from planner,")
    assert "planner is waiting for your reply text" in asked and "SendMessage" not in asked
    # The asker reads nothing else meanwhile: a question back would wait out both limits.
    assert "don't ask it anything first" in asked
