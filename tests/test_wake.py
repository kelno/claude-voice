"""Sessions a shutdown interrupted are woken when the bridge starts, so the owner finds them on the phone.

A shutdown stops every background session, and Claude Code shows each as failed; nothing restarts
them. The bridge stops with them and starts again at logon, so it wakes the ones it started or
resumed that were running when it last looked. Windows' boot time can't tell: with Fast Startup a
shutdown resumes the old kernel, and the boot time stays weeks old.
"""

import asyncio
import logging

from fakes import FakeClaude
from mcp.client import Client
from test_start import Starter

from claude_voice import transcripts
from claude_voice.server import build_server, wake_interrupted
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


def job(sid, cwd, state="failed", name=None):
    return {"id": sid[:8], "sessionId": sid, "cwd": str(cwd), "state": state, "kind": "background", "name": name or sid}


def setup(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    return work, Store(tmp_path / "b.db")


async def test_a_session_the_bridge_started_that_a_shutdown_interrupted_is_woken(tmp_path, caplog):
    work, store = setup(tmp_path)
    store.record_started("s-mine", "Triplicate", str(work))
    starter = Starter(tmp_path, register=False)

    async def jobs():
        return [job("s-mine", work, name="Triplicate")]

    with caplog.at_level(logging.INFO, logger="claude_voice"):
        woken = await wake_interrupted(store, jobs, {"s-mine"}, starter)
    assert woken == ["Triplicate"]
    assert starter.calls == [(str(work), "")]
    assert starter.options == [{"resume": "s-mine", "wake": True}]
    assert any("Triplicate" in r.message and "woke" in r.message for r in caplog.records)


async def test_only_sessions_the_shutdown_interrupted_and_the_bridge_ran_are_woken(tmp_path):
    work, store = setup(tmp_path)
    for sid in ("s-stopped", "s-failed-earlier", "s-done"):
        store.record_started(sid, sid, str(work))
    starter = Starter(tmp_path, register=False)

    async def jobs():
        return [
            job("s-stopped", work, state="stopped"),  # stopped on purpose
            job("s-failed-earlier", work),  # failed while the bridge watched: gone from its last look
            job("s-done", work, state="done"),
            job("s-not-ours", work),  # the owner's own background session
        ]

    assert await wake_interrupted(store, jobs, {"s-stopped", "s-done", "s-not-ours"}, starter) == []
    assert starter.calls == []


async def test_a_session_that_will_not_wake_is_logged_and_the_others_still_are(tmp_path, caplog):
    work, store = setup(tmp_path)
    for sid in ("s-broken", "s-fine"):
        store.record_started(sid, sid, str(work))

    async def starter(cwd, name, **options):
        if options["resume"] == "s-broken":
            raise RuntimeError("Workspace not trusted")

    async def jobs():
        return [job("s-broken", work), job("s-fine", work)]

    with caplog.at_level(logging.INFO, logger="claude_voice"):
        woken = await wake_interrupted(store, jobs, {"s-broken", "s-fine"}, starter)
    assert woken == ["s-fine"]
    assert any("s-broken" in r.message and "Workspace not trusted" in r.message for r in caplog.records)


async def test_the_served_bridge_wakes_what_was_running_at_its_last_look(tmp_path, monkeypatch):
    class Reader:  # the served bridge warms its side-question reader; no real one here
        async def warm(self):
            pass

    monkeypatch.setattr(transcripts, "shared_reader", Reader)
    work, store = setup(tmp_path)
    store.record_started("s-mine", "Triplicate", str(work))
    live = tmp_path / "live"
    live.mkdir()  # nothing runs now: the watcher's first look forgets the last one
    starter = Starter(tmp_path, register=False)

    async def jobs():
        return [job("s-mine", work, name="Triplicate")]

    m = SessionManager(store, project_root=work, client_factory=FakeClaude())

    async def up():
        return True

    srv = build_server(
        m, live_dir=live, watch_interval=60.0, start_background=starter, background_jobs=jobs, connectors_reachable=up
    )
    with store.db:  # what the bridge saw running before the shutdown
        store.db.execute("INSERT INTO live_snapshot VALUES(?,?,?)", ("Triplicate", "idle", "s-mine"))
    async with Client(srv) as c:
        for _ in range(100):
            if starter.calls:
                break
            await asyncio.sleep(0.01)
        await c.list_tools()
    assert starter.options == [{"resume": "s-mine", "wake": True}]


async def test_the_sessions_to_wake_wait_until_claude_ai_answers(tmp_path):
    # Seen 2026-11-13: the bridge woke a session at logon before the network was back after a
    # sleep, and the session ran without any claude.ai connector (ledger Q104).
    work, store = setup(tmp_path)
    store.record_started("s-mine", "Triplicate", str(work))
    order = []
    answers = iter([False, False, True])

    async def ready():
        order.append("asked")
        return next(answers)

    async def starter(cwd, name, **options):
        order.append("woken")

    async def jobs():
        return [job("s-mine", work, name="Triplicate")]

    woken = await wake_interrupted(store, jobs, {"s-mine"}, starter, ready=ready, retry=0)
    assert woken == ["Triplicate"]
    assert order == ["asked", "asked", "asked", "woken"]


async def test_the_sessions_are_woken_anyway_when_claude_ai_never_answers(tmp_path, caplog):
    work, store = setup(tmp_path)
    store.record_started("s-mine", "Triplicate", str(work))
    starter = Starter(tmp_path, register=False)

    async def never():
        return False

    async def jobs():
        return [job("s-mine", work, name="Triplicate")]

    with caplog.at_level(logging.INFO, logger="claude_voice"):
        woken = await wake_interrupted(store, jobs, {"s-mine"}, starter, ready=never, retry=0, patience=0.05)
    assert woken == ["Triplicate"]
    assert any("claude.ai" in r.message for r in caplog.records)


async def test_nothing_to_wake_asks_nothing_of_claude_ai(tmp_path):
    work, store = setup(tmp_path)
    asked = []

    async def ready():
        asked.append(1)
        return True

    async def jobs():
        return []

    assert await wake_interrupted(store, jobs, set(), Starter(tmp_path, register=False), ready=ready) == []
    assert asked == []
