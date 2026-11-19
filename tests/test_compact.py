"""A session compacts its own conversation at a break it chooses, without the owner typing /compact.

The session calls compact_after_turn and ends its turn; once the turn has ended, the bridge stops
it, runs /compact on the stopped conversation with `claude -p --resume`, and wakes it in place.
Claude Code runs /compact without a terminal (tried on a throwaway session, 2026-11-13: the
conversation was compacted in the same transcript, the PreCompact hook ran, and the woken session
showed it compacted). The owner's wish (2026-11-13, ledger F32): the session picks the moment.
"""

import asyncio
import json

import pytest
from fakes import FakeClaude
from mcp.client import Client
from test_confirm import call, outcome, reached, transcript

from claude_voice import confirm, jobs, procs, server
from claude_voice.server import build_server, compact_when_idle
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


def test_the_compaction_runs_headless_on_the_stopped_conversation_with_its_instructions():
    command = jobs.compact_command("claude", "sid-1", "Keep the open questions.")
    assert command == ["claude", "-p", "/compact Keep the open questions.", "--resume", "sid-1"]
    assert jobs.compact_command("claude", "sid-1") == ["claude", "-p", "/compact", "--resume", "sid-1"]


class Steps:
    """What compact_when_idle did, in order, against a session whose states are given in turn."""

    def __init__(self, states, compact_fails=False):
        self.states = list(states)
        self.done: list[str] = []
        self.compact_fails = compact_fails

    def state(self):
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    async def stop(self):
        self.done.append("stop")

    async def compact(self):
        self.done.append("compact")
        if self.compact_fails:
            raise RuntimeError("compaction failed")

    async def wake(self):
        self.done.append("wake")

    async def run(self, **kw):
        async def no_wait(_):
            return None

        return await compact_when_idle(self.state, self.stop, self.compact, self.wake, sleep=no_wait, **kw)


async def test_the_session_is_stopped_compacted_and_woken_only_once_its_turn_has_ended():
    steps = Steps(["busy", "busy", "idle", "idle"])
    assert await steps.run() == "compacted"
    assert steps.done == ["stop", "compact", "wake"]


async def test_a_turn_counts_as_ended_only_once_the_session_is_seen_at_rest_twice_in_a_row():
    steps = Steps(["busy", "idle", "busy", "idle", "idle"])
    await steps.run()
    assert steps.states == ["idle"]  # every look was used up before stopping


async def test_a_session_stopped_to_be_compacted_is_always_woken_again_compacted_or_not():
    steps = Steps(["idle"], compact_fails=True)
    assert await steps.run() == "compact_failed"
    assert steps.done == ["stop", "compact", "wake"]


async def test_a_session_that_ended_or_never_rested_is_left_alone():
    ended = Steps(["busy", "ended"])
    assert await ended.run() == "ended"
    never = Steps(["busy"])
    assert await never.run(give_up=10.0, poll=2.0) == "gave_up"
    assert ended.done == never.done == []


# -- through the bridge, as a session calls it -------------------------------------------


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setattr(procs, "alive", lambda pid: True)
    monkeypatch.setattr(confirm, "FIND_SECONDS", 0.25)
    monkeypatch.setattr(server, "COMPACT_POLL_SECONDS", 0.05)
    home, live, projects = tmp_path / "home", tmp_path / "live", tmp_path / "projects"
    for d in (home, live, home / "app"):
        d.mkdir()
    entry = {"pid": 7, "sessionId": "sid-7", "cwd": str(home / "app"), "name": "desk", "status": "busy", "kind": "bg"}
    (live / "7.json").write_text(json.dumps(entry), encoding="utf-8")
    transcript(projects, home / "app", "sid-7", "toolu_compact")
    store = Store(tmp_path / "b.db")
    done: list[tuple] = []
    woken: dict = {}  # the registry entry a wake writes back, when a test gives one
    held = asyncio.Event()  # the compaction runs once this is set; a test may clear it
    held.set()
    broken: list[str] = []  # a test puts an error here to have the compaction fail with it

    async def stop(sid):
        done.append(("stop", sid))
        (live / "7.json").unlink()

    async def compact(cwd, sid, instructions):
        done.append(("compact", cwd, sid, instructions))
        await held.wait()
        if broken:
            raise RuntimeError(broken[0])

    async def wake(cwd, session_id):
        done.append(("wake", cwd, session_id))
        if woken:
            (live / "7.json").write_text(json.dumps(woken), encoding="utf-8")

    async def deliver(name, text):
        done.append(("told", name, text))
        return {"delivered": True}

    bridge_server = build_server(
        SessionManager(store, project_root=home, client_factory=FakeClaude()),
        conversations=lambda **k: [],
        live_dir=live,
        deliver=deliver,
        projects_dir=projects,
        claude_config=tmp_path / "claude.json",
        stop_background=stop,
        wake_background=wake,
        compact_stopped=compact,
    )
    return {
        "server": bridge_server,
        "store": store,
        "live": live,
        "entry": entry,
        "done": done,
        "woken": woken,
        "held": held,
        "broken": broken,
        "cwd": str(home / "app"),
    }


async def test_a_session_the_bridge_started_is_compacted_after_the_turn_that_asked(bridge):
    bridge["store"].record_started("sid-7", "desk", bridge["cwd"])
    async with reached(bridge["server"]) as app:
        asked = outcome(
            await call(app, "compact_after_turn", {"instructions": "Keep the plan."}, tool_use_id="toolu_compact")
        )
        assert asked["status"] == "scheduled"
        await asyncio.sleep(0.3)
        assert bridge["done"] == []  # still mid-turn: nothing happens yet
        (bridge["live"] / "7.json").write_text(json.dumps({**bridge["entry"], "status": "idle"}), encoding="utf-8")
        for _ in range(100):
            if any(step[0] == "wake" for step in bridge["done"]):
                break
            await asyncio.sleep(0.05)
    assert bridge["done"] == [
        ("stop", "sid-7"),
        ("compact", bridge["cwd"], "sid-7", "Keep the plan."),
        ("wake", bridge["cwd"], "sid-7"),
    ]


async def test_a_message_to_a_session_stopped_for_its_compaction_says_it_is_coming_back(bridge):
    # Seen 2026-11-14: a message during the minute a session was stopped to be compacted was told
    # "not running ... attach_conversation resumes it", and the relay went resuming a session the
    # bridge was already bringing back.
    bridge["store"].record_started("sid-7", "desk", bridge["cwd"])
    bridge["held"].clear()
    (bridge["live"] / "7.json").write_text(json.dumps({**bridge["entry"], "status": "idle"}), encoding="utf-8")
    async with reached(bridge["server"]) as app:
        await call(app, "compact_after_turn", {}, tool_use_id="toolu_compact")
        for _ in range(100):
            if any(step[0] == "compact" for step in bridge["done"]):
                break
            await asyncio.sleep(0.05)
        async with Client(bridge["server"]) as c:
            refused = await c.call_tool("message_active_session", {"session": "desk", "message": "Hi"})
        bridge["held"].set()
    text = refused.content[0].text
    assert refused.is_error and "being compacted" in text and "under a minute" in text
    assert "attach_conversation" not in text


async def test_only_a_background_session_is_stopped_to_be_compacted(bridge):
    # One in a terminal window: Claude Code compacts and wakes only background sessions.
    (bridge["live"] / "7.json").write_text(json.dumps({**bridge["entry"], "kind": "interactive"}), encoding="utf-8")
    async with reached(bridge["server"]) as app:
        refused = await call(app, "compact_after_turn", {}, tool_use_id="toolu_compact")
    assert refused.get("isError")
    assert "/compact" in refused["content"][0]["text"]
    assert bridge["done"] == []


async def test_a_background_session_the_owner_started_is_compacted_too(bridge):
    # Never recorded as started by the bridge: the owner started it.
    async with reached(bridge["server"]) as app:
        asked = outcome(await call(app, "compact_after_turn", {}, tool_use_id="toolu_compact"))
    assert asked["status"] == "scheduled"


async def test_a_compaction_given_up_is_told_to_the_session_with_why(bridge, monkeypatch, caplog):
    monkeypatch.setattr(server, "COMPACT_GIVE_UP_SECONDS", 0.2)
    bridge["store"].record_started("sid-7", "desk", bridge["cwd"])
    # Its turn has ended but Claude Code says a background task still runs: never at rest.
    (bridge["live"] / "7.json").write_text(json.dumps({**bridge["entry"], "status": "shell"}), encoding="utf-8")
    caplog.set_level("INFO", logger="claude_voice")
    async with reached(bridge["server"]) as app:
        await call(app, "compact_after_turn", {}, tool_use_id="toolu_compact")
        for _ in range(100):
            if bridge["done"]:
                break
            await asyncio.sleep(0.05)
    [(step, name, text)] = bridge["done"]
    assert (step, name) == ("told", "desk")
    assert "gave up" in text and "background task still running" in text and "compact_after_turn" in text
    assert any("gave_up" in r.message and "background task still running" in r.message for r in caplog.records)


@pytest.mark.parametrize("linked", [True, False])
async def test_a_session_woken_from_a_compaction_without_its_remote_control_link_is_logged(
    bridge, monkeypatch, caplog, linked
):
    # The wake un-archives the claude.ai session and reattaches to it; where claude.ai refuses
    # (an untrusted device), the session runs on without Remote Control, out of the app's list.
    monkeypatch.setattr(server, "RELISTED_SECONDS", 0.3)
    bridge["store"].record_started("sid-7", "desk", bridge["cwd"])
    remote = {**bridge["entry"], "status": "idle", "bridgeSessionId": "session_1"}
    (bridge["live"] / "7.json").write_text(json.dumps(remote), encoding="utf-8")
    bridge["woken"].update(remote if linked else bridge["entry"], status="idle")
    caplog.set_level("INFO", logger="claude_voice")
    async with reached(bridge["server"]) as app:
        await call(app, "compact_after_turn", {}, tool_use_id="toolu_compact")
        for _ in range(100):
            if any("compact_after_turn desk" in r.message for r in caplog.records):
                break
            await asyncio.sleep(0.05)
    warned = [r for r in caplog.records if "without Remote Control" in r.message]
    assert bool(warned) is not linked


@pytest.mark.parametrize("failed", [False, True])
async def test_a_session_woken_from_its_compaction_is_told_to_carry_on(bridge, monkeypatch, failed):
    # Seen 2026-11-19 (ledger F119): Claude Code wakes the session with no turn to take, so it sat
    # idle until the owner told it to resume.
    monkeypatch.setattr(server, "RELISTED_SECONDS", 0.3)
    if failed:
        bridge["broken"].append("claude -p /compact exited 1")
    bridge["store"].record_started("sid-7", "desk", bridge["cwd"])
    (bridge["live"] / "7.json").write_text(json.dumps({**bridge["entry"], "status": "idle"}), encoding="utf-8")
    bridge["woken"].update(bridge["entry"], status="idle")
    async with reached(bridge["server"]) as app:
        await call(app, "compact_after_turn", {}, tool_use_id="toolu_compact")
        for _ in range(100):
            if any(step[0] == "told" for step in bridge["done"]):
                break
            await asyncio.sleep(0.05)
    steps = [step[0] for step in bridge["done"]]
    assert steps[-2:] == ["wake", "told"]
    [(_, name, text)] = [step for step in bridge["done"] if step[0] == "told"]
    assert name == "desk" and "carry on" in text.lower()
    assert ("failed" in text) is failed
