"""Sessions named in config.toml's `wake_at_start` are resumed whenever the bridge starts, unless they run.

The bridge already wakes what a shutdown interrupted, but only what it saw running at its last look.
At a shutdown Windows may end a session a moment before the bridge, which then misses it (seen
2026-11-21: the session that coordinates the others stayed down after an update restart). The owner
names the sessions that must always be up; each start, after a boot or a restart of the bridge
alone, resumes those that aren't running, in place, as attach_conversation does, and leaves the
running ones alone.
"""

import asyncio
import json
import logging

from fakes import FakeClaude
from mcp.client import Client
from test_start import PID, Starter

from claude_voice import config, transcripts, wake_at_start
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

COORDINATOR = "252eb71d-d82d-4a4b-9212-8ba291e1e692"


def settings(tmp_path, *named):
    path = tmp_path / "config.toml"
    path.write_text("wake_at_start = [" + ", ".join(json.dumps(n) for n in named) + "]\n", encoding="utf-8")
    return path


def test_the_sessions_to_wake_at_start_are_named_in_config_toml(tmp_path):
    assert wake_at_start.configured(settings(tmp_path, "Coordinator", COORDINATOR)) == ["Coordinator", COORDINATOR]


def test_no_file_or_no_list_names_no_session(tmp_path):
    assert wake_at_start.configured(None) == []
    assert wake_at_start.configured(tmp_path / "missing.toml") == []
    other = tmp_path / "other.toml"
    other.write_text('wake_at_start = "Coordinator"\n', encoding="utf-8")
    assert wake_at_start.configured(other) == []


class World:
    """What wake() is given: which sessions run, which it knows, and a resume that records."""

    def __init__(self, running=(), known=None, broken=()):
        self.running = set(running)
        self.records = known or {}
        self.broken = set(broken)
        self.resumed = []

    def runs(self, session):
        return session in self.running

    def known(self, session):
        return self.records.get(session)

    async def resume(self, found):
        if found["claude_session_id"] in self.broken:
            raise RuntimeError("Workspace not trusted")
        self.resumed.append(found["claude_session_id"])


def record(sid, name, cwd="D:/vault"):
    return {"claude_session_id": sid, "name": name, "cwd": cwd}


async def test_a_session_named_to_wake_at_start_is_resumed_when_it_is_not_running(caplog):
    world = World(known={"Coordinator": record(COORDINATOR, "Coordinator")})
    with caplog.at_level(logging.INFO, logger="claude_voice"):
        woken = await wake_at_start.wake(["Coordinator"], world.runs, world.known, world.resume)
    assert woken == [COORDINATOR]
    assert world.resumed == [COORDINATOR]
    assert any("Coordinator" in r.message and "woke" in r.message for r in caplog.records)


async def test_a_session_named_to_wake_at_start_that_already_runs_is_left_alone():
    # A restart of the bridge alone, after a landing: the session runs on, untouched.
    world = World(running={"Coordinator"}, known={"Coordinator": record(COORDINATOR, "Coordinator")})
    assert await wake_at_start.wake(["Coordinator"], world.runs, world.known, world.resume) == []
    assert world.resumed == []


async def test_a_session_running_under_its_id_is_left_alone_when_named_by_an_old_name():
    world = World(running={COORDINATOR}, known={"Coord": record(COORDINATOR, "Coordinator")})
    assert await wake_at_start.wake(["Coord"], world.runs, world.known, world.resume) == []
    assert world.resumed == []


async def test_a_name_no_session_answers_to_is_logged_and_nothing_is_started(caplog):
    world = World()
    with caplog.at_level(logging.INFO, logger="claude_voice"):
        assert await wake_at_start.wake(["Nobody"], world.runs, world.known, world.resume) == []
    assert world.resumed == []
    assert any("Nobody" in r.message and "wake_at_start" in r.message for r in caplog.records)


async def test_a_session_that_will_not_resume_is_logged_and_the_others_still_are(caplog):
    world = World(known={"A": record("s-a", "A"), "B": record("s-b", "B")}, broken={"s-a"})
    with caplog.at_level(logging.INFO, logger="claude_voice"):
        assert await wake_at_start.wake(["A", "B"], world.runs, world.known, world.resume) == ["s-b"]
    assert any("s-a" in r.message and "Workspace not trusted" in r.message for r in caplog.records)


async def test_a_session_named_twice_is_resumed_once():
    world = World(known={"Coordinator": record(COORDINATOR, "Coordinator"), COORDINATOR: record(COORDINATOR, "C")})
    assert await wake_at_start.wake(["Coordinator", COORDINATOR], world.runs, world.known, world.resume) == [
        COORDINATOR
    ]
    assert world.resumed == [COORDINATOR]


async def test_the_sessions_to_wake_at_start_wait_until_claude_ai_answers():
    order = []
    answers = iter([False, True])

    async def ready():
        order.append("asked")
        return next(answers)

    world = World(known={"Coordinator": record(COORDINATOR, "Coordinator")})

    async def resume(found):
        order.append("resumed")

    await wake_at_start.wake(["Coordinator"], world.runs, world.known, resume, ready=ready, retry=0)
    assert order == ["asked", "asked", "resumed"]


async def test_nothing_to_wake_at_start_asks_nothing_of_claude_ai():
    asked = []

    async def ready():
        asked.append(1)
        return True

    world = World(running={"Coordinator"})
    await wake_at_start.wake(["Coordinator"], world.runs, world.known, world.resume, ready=ready)
    assert asked == []


# The served bridge: the setting read from config.toml, the session found from Claude Code's job
# record, resumed through the same path as attach_conversation.


class Reader:  # the served bridge warms its side-question reader; no real one here
    async def warm(self):
        pass


def served(tmp_path, monkeypatch, jobs_listed, starter, was_live=()):
    monkeypatch.setattr(transcripts, "shared_reader", Reader)
    work = tmp_path / "vault"
    work.mkdir()
    jobs_dir = tmp_path / "jobs"
    (jobs_dir / COORDINATOR[:8]).mkdir(parents=True)
    (jobs_dir / COORDINATOR[:8] / "state.json").write_text(
        json.dumps({"sessionId": COORDINATOR, "name": "Coordinator", "cwd": str(work), "updatedAt": "2026-11-21"}),
        encoding="utf-8",
    )
    live = tmp_path / "live"
    live.mkdir(exist_ok=True)
    store = Store(tmp_path / "b.db")

    async def jobs():
        return jobs_listed(work)

    async def background():
        return {j["sessionId"] for j in jobs_listed(work)}

    async def up():
        return True

    srv = build_server(
        SessionManager(store, project_root=tmp_path, client_factory=FakeClaude()),
        live_dir=live,
        watch_interval=60.0,
        wake_background=starter.wake,
        background_jobs=jobs,
        background_sessions=background,
        connectors_reachable=up,
        jobs_dir=jobs_dir,
        repo_config=config.load(settings(tmp_path, "Coordinator"), env={"HOME": str(tmp_path)}),
    )
    with store.db:  # what the bridge saw running before the shutdown
        for sid in was_live:
            store.db.execute("INSERT INTO live_snapshot VALUES(?,?,?)", ("Coordinator", "idle", sid))
    return srv, work, live


async def settle(starter):
    for _ in range(100):
        if starter.woken:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)  # room for a second, wrong wake


def ended(work):
    return [{"sessionId": COORDINATOR, "cwd": str(work), "state": "done", "name": "Coordinator"}]


async def test_the_served_bridge_resumes_a_session_named_to_wake_at_start_it_did_not_see_running(tmp_path, monkeypatch):
    # Seen 2026-11-21: the session ended a moment before the bridge at a shutdown, so the bridge's
    # last look no longer had it and nothing woke it.
    starter = Starter(tmp_path / "live", register=True)
    (tmp_path / "live").mkdir()
    srv, work, _ = served(tmp_path, monkeypatch, ended, starter)
    async with Client(srv) as c:
        await settle(starter)
        await c.list_tools()
    assert starter.woken == [(str(work), COORDINATOR)]


async def test_the_served_bridge_leaves_a_session_named_to_wake_at_start_alone_when_it_runs(tmp_path, monkeypatch):
    starter = Starter(tmp_path / "live", register=True)
    (tmp_path / "live").mkdir()
    srv, work, live = served(tmp_path, monkeypatch, ended, starter)
    entry = {"pid": PID, "sessionId": COORDINATOR, "cwd": str(work), "name": "Coordinator", "kind": "bg"}
    (live / "coordinator.json").write_text(json.dumps(entry), encoding="utf-8")
    async with Client(srv) as c:
        await settle(starter)
        await c.list_tools()
    assert starter.woken == []


async def test_a_session_already_resumed_because_it_is_named_to_wake_at_start_is_not_woken_again(tmp_path, monkeypatch):
    starter = Starter(tmp_path / "live", register=True)
    (tmp_path / "live").mkdir()
    srv, work, _ = served(tmp_path, monkeypatch, ended, starter, was_live=[COORDINATOR])
    async with Client(srv) as c:
        await settle(starter)
        await c.list_tools()
    assert starter.woken == [(str(work), COORDINATOR)]
