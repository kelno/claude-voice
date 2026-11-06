"""A finished turn's last message comes back whole, so a long report needs no further reads.

Reports run to five or six thousand characters; cut at max_chars, they took two or three more
reads while the owner waited in silence, and the relay sometimes answered before reading it all.
max_chars still applies to messages of a turn still running and to earlier turns. A final reply
past FINAL_CHARS is cut there and says so.
"""

import asyncio

import pytest
from mcp.client import Client
from test_ask import Target, bridge, call
from test_live import msg

from claude_voice.server import FINAL_CHARS

REPORT = "Report. " + "word " * 1200  # about 6,000 characters


@pytest.fixture
def root(tmp_path):
    (tmp_path / "src" / "billing").mkdir(parents=True)
    return tmp_path / "src"


@pytest.fixture
def live(tmp_path):
    d = tmp_path / "live"
    d.mkdir()
    return d


async def nothing(name, text):
    return {"delivered": True, "detail": "DELIVERED"}


async def test_wait_returns_a_finished_turns_last_message_whole(tmp_path, root, live):
    target = Target(live, str(root / "billing"))
    target.says("Working on it. " * 80)
    target.says(REPORT)
    async with Client(bridge(tmp_path, root, live, target, nothing)) as c:
        out = await call(c, "wait_for_session", session="billing-ab", after=1)
    assert out["reply_final"] is True
    assert out["reply"] == REPORT
    # Each message in turns keeps max_chars: the report is whole once, in reply, not twice.
    assert [len(t["text"]) <= 600 for t in out["turns"]] == [True, True]


async def test_a_message_of_a_turn_still_running_keeps_max_chars(tmp_path, root, live):
    target = Target(live, str(root / "billing"), status="busy")
    target.says(REPORT)
    async with Client(bridge(tmp_path, root, live, target, nothing)) as c:
        out = await call(c, "wait_for_session", session="billing-ab", after=1, wait_seconds=0.05)
    assert out["reply_final"] is False
    assert len(out["reply"]) <= 600


async def test_ask_returns_the_final_reply_whole(tmp_path, root, live):
    target = Target(live, str(root / "billing"))

    async def deliver(name, text):
        async def work():
            await asyncio.sleep(0.05)
            target.set("busy")
            target.transcript.append(msg("user", text))
            target.says(REPORT)
            target.set("idle")

        asyncio.get_running_loop().create_task(work())
        return {"delivered": True, "detail": "DELIVERED"}

    async with Client(bridge(tmp_path, root, live, target, deliver)) as c:
        out = await call(c, "ask_active_session", session="billing-ab", message="Report?", wait_seconds=5)
    assert out["reply_final"] is True
    assert out["reply"] == REPORT


async def test_read_returns_the_last_message_of_a_finished_turn_whole(tmp_path, root, live):
    target = Target(live, str(root / "billing"))
    target.says(REPORT)
    async with Client(bridge(tmp_path, root, live, target, nothing)) as c:
        out = await call(c, "read_session_output", session="billing-ab", after=1)
    last = out["turns"][-1]
    assert last["text"] == REPORT and last["cut"] is False


async def test_a_final_reply_past_the_high_cap_is_cut_there_and_says_so(tmp_path, root, live):
    target = Target(live, str(root / "billing"))
    huge = "word " * (FINAL_CHARS // 4)
    target.says(huge)
    async with Client(bridge(tmp_path, root, live, target, nothing)) as c:
        out = await call(c, "wait_for_session", session="billing-ab", after=1)
        read = await call(c, "read_session_output", session="billing-ab", after=1)
    assert len(out["reply"]) <= FINAL_CHARS
    assert out["reply_cut"] is True and out["reply_length"] == len(huge)
    assert read["turns"][-1]["cut"] is True and read["turns"][-1]["length"] == len(huge)
