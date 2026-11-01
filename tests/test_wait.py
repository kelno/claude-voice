"""Waiting for a running session's turn to end, without sending it anything."""

import asyncio
import json
import os
import time

import pytest
from mcp.client import Client
from test_ask import Target, bridge, call, continued
from test_live import msg


@pytest.fixture
def root(tmp_path):
    (tmp_path / "src" / "billing").mkdir(parents=True)
    return tmp_path / "src"


@pytest.fixture
def live(tmp_path):
    d = tmp_path / "live"
    d.mkdir()
    return d


async def test_waiting_returns_once_the_turn_has_ended(tmp_path, root, live):
    target = Target(live, str(root / "billing"), status="busy")

    async def work():
        await asyncio.sleep(0.05)
        target.says("Done: the export is fixed.")
        target.set("idle")

    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        asyncio.get_running_loop().create_task(work())
        out = await call(c, "wait_for_session", session="billing-ab", wait_seconds=5)
    assert out["status"] == "idle"
    assert out["reply"] == "Done: the export is fixed."
    assert out["reply_final"] is True
    assert out["next_after"] == 2


async def test_with_after_the_wait_is_for_a_reply_past_that_turn(tmp_path, root, live):
    # Seen live: a message is delivered, the session has not picked it up yet, so it looks
    # idle; the relay must not be told its turn has ended before it has even begun.
    target = Target(live, str(root / "billing"))

    async def work():
        await asyncio.sleep(0.05)
        target.set("busy")
        target.transcript.append(msg("user", "ping"))
        target.says("pong")
        target.set("idle")

    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        asyncio.get_running_loop().create_task(work())
        out = await call(c, "wait_for_session", session="billing-ab", after=1, wait_seconds=5)
    assert out["status"] == "idle"
    assert [t["text"] for t in out["turns"]] == ["pong"]
    assert out["reply"] == "pong" and out["next_after"] == 3


async def test_an_idle_session_with_nothing_asked_of_it_returns_at_once(tmp_path, root, live):
    target = Target(live, str(root / "billing"))
    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        started = time.monotonic()
        out = await call(c, "wait_for_session", session="billing-ab", wait_seconds=5)
    assert time.monotonic() - started < 1
    assert out["status"] == "idle" and out["reply"] == "earlier answer"


async def test_a_turn_still_going_at_the_cap_says_so_with_the_latest_words(tmp_path, root, live):
    target = Target(live, str(root / "billing"), status="busy")
    target.says("Starting on it.")
    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        out = await call(c, "wait_for_session", session="billing-ab", wait_seconds=0.2)
    assert out["status"] == "still_working"
    assert out["reply"] == "Starting on it." and out["reply_final"] is False


async def test_a_session_that_needs_input_ends_the_wait(tmp_path, root, live):
    target = Target(live, str(root / "billing"), status="busy")

    async def work():
        await asyncio.sleep(0.05)
        target.file.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "sessionId": "sid-1",
                    "cwd": target.cwd,
                    "name": target.name,
                    "status": "waiting",
                    "waitingFor": "permission prompt",
                }
            )
        )

    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        asyncio.get_running_loop().create_task(work())
        out = await call(c, "wait_for_session", session="billing-ab", wait_seconds=5)
    assert out["status"] == "needs_input"


async def test_a_session_that_exits_during_the_wait_is_reported(tmp_path, root, live):
    target = Target(live, str(root / "billing"), status="busy")

    async def work():
        await asyncio.sleep(0.05)
        target.file.unlink()

    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        asyncio.get_running_loop().create_task(work())
        out = await call(c, "wait_for_session", session="billing-ab", wait_seconds=5)
    assert out["status"] == "session_ended" and out["session_ended"] is True


async def test_a_session_continued_elsewhere_is_shown_as_moved(tmp_path, root, live):
    target = Target(live, str(root / "billing"))
    continued(tmp_path, str(root / "billing"), "sid-1", "sid-2")
    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        out = await call(c, "wait_for_session", session="billing-ab")
    assert out["status"] == "moved" and out["moved_to"]["id"] == "sid-2"
