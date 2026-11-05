"""Sessions a shutdown interrupted are woken when the bridge starts, so the owner finds them on the phone.

A reboot stops every background session, and Claude Code shows each as failed for 48 hours;
nothing restarts them. The bridge starts at logon, so it wakes the ones it started or resumed.
"""

import asyncio
import logging
import os
import time

from fakes import FakeClaude
from mcp.client import Client
from test_start import Starter

from claude_voice import transcripts
from claude_voice.server import build_server, wake_interrupted
from claude_voice.sessions import SessionManager
from claude_voice.store import Store
from claude_voice.transcripts import project_key_for_directory

BOOT = time.time() - 600  # the PC came up ten minutes ago


def transcript(projects, cwd, sid, at):
    path = projects / project_key_for_directory(str(cwd)) / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", encoding="utf-8")
    os.utime(path, (at, at))


def job(sid, cwd, state="failed", name=None):
    return {"id": sid[:8], "sessionId": sid, "cwd": str(cwd), "state": state, "kind": "background", "name": name or sid}


def setup(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    projects = tmp_path / "projects"
    store = Store(tmp_path / "b.db")
    return work, projects, store


async def test_a_session_the_bridge_started_that_a_shutdown_interrupted_is_woken(tmp_path, caplog):
    work, projects, store = setup(tmp_path)
    store.record_started("s-mine", "Triplicate", str(work))
    transcript(projects, work, "s-mine", BOOT - 3600)
    starter = Starter(tmp_path, register=False)

    async def jobs():
        return [job("s-mine", work, name="Triplicate")]

    with caplog.at_level(logging.INFO, logger="claude_voice"):
        woken = await wake_interrupted(store, jobs, lambda: BOOT, projects, starter)
    assert woken == ["Triplicate"]
    assert starter.calls == [(str(work), "")]
    assert starter.options == [{"resume": "s-mine", "wake": True}]
    assert any("Triplicate" in r.message and "woke" in r.message for r in caplog.records)


async def test_only_sessions_the_shutdown_interrupted_and_the_bridge_ran_are_woken(tmp_path):
    work, projects, store = setup(tmp_path)
    for sid in ("s-stopped", "s-failed-since-boot", "s-old", "s-done"):
        store.record_started(sid, sid, str(work))
    transcript(projects, work, "s-stopped", BOOT - 60)
    transcript(projects, work, "s-failed-since-boot", BOOT + 60)  # failed on its own, PC up
    transcript(projects, work, "s-old", BOOT - 3 * 24 * 3600)  # past Claude Code's 48 hours
    transcript(projects, work, "s-done", BOOT - 60)
    transcript(projects, work, "s-not-ours", BOOT - 60)  # the owner's own background session
    starter = Starter(tmp_path, register=False)

    async def jobs():
        return [
            job("s-stopped", work, state="stopped"),  # stopped on purpose
            job("s-failed-since-boot", work),
            job("s-old", work),
            job("s-done", work, state="done"),
            job("s-not-ours", work),
            job("s-no-transcript", work),
        ]

    assert await wake_interrupted(store, jobs, lambda: BOOT, projects, starter) == []
    assert starter.calls == []


async def test_a_session_that_will_not_wake_is_logged_and_the_others_still_are(tmp_path, caplog):
    work, projects, store = setup(tmp_path)
    for sid in ("s-broken", "s-fine"):
        store.record_started(sid, sid, str(work))
        transcript(projects, work, sid, BOOT - 60)

    async def starter(cwd, name, **options):
        if options["resume"] == "s-broken":
            raise RuntimeError("Workspace not trusted")

    async def jobs():
        return [job("s-broken", work), job("s-fine", work)]

    with caplog.at_level(logging.INFO, logger="claude_voice"):
        woken = await wake_interrupted(store, jobs, lambda: BOOT, projects, starter)
    assert woken == ["s-fine"]
    assert any("s-broken" in r.message and "Workspace not trusted" in r.message for r in caplog.records)


async def test_the_served_bridge_wakes_interrupted_sessions_when_it_starts(tmp_path, monkeypatch):
    class Reader:  # the served bridge warms its side-question reader; no real one here
        async def warm(self):
            pass

    monkeypatch.setattr(transcripts, "shared_reader", Reader)
    work, projects, store = setup(tmp_path)
    store.record_started("s-mine", "Triplicate", str(work))
    transcript(projects, work, "s-mine", BOOT - 60)
    live = tmp_path / "live"
    live.mkdir()
    starter = Starter(tmp_path, register=False)

    async def jobs():
        return [job("s-mine", work, name="Triplicate")]

    m = SessionManager(store, project_root=work, client_factory=FakeClaude())
    srv = build_server(
        m,
        live_dir=live,
        projects_dir=projects,
        watch_interval=60.0,
        start_background=starter,
        background_jobs=jobs,
        boot_time=lambda: BOOT,
    )
    async with Client(srv) as c:
        for _ in range(100):
            if starter.calls:
                break
            await asyncio.sleep(0.01)
        await c.list_tools()
    assert starter.options == [{"resume": "s-mine", "wake": True}]
