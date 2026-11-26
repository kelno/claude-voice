"""Starting a background Claude Code session that the voice can talk to at once."""

import json
import os
from types import SimpleNamespace

import pytest
from fakes import FakeClaude
from mcp.client import Client

from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

PID = os.getpid()


class Starter:
    """Stands in for `claude --bg`: records the call and registers the session like Claude Code does."""

    def __init__(self, live, register=True, copies=False, note=None, entry=None):
        self.live = live
        self.register = register
        self.copies = copies  # Claude Code couldn't continue the conversation in place
        self.note = note  # what Claude Code prints, e.g. a "note:" line
        self.entry = entry or {}  # more of the registry entry, e.g. a Remote Control link or a status
        self.calls = []
        self.options = []
        self.woken = []

    async def __call__(self, cwd, name, **how):
        self.calls.append((cwd, name))
        self.options.append(how)
        sid = how.get("resume") if how.get("resume") and not self.copies else f"sid-{name}"
        return self.registered(cwd, name, sid)

    async def wake(self, cwd, session_id):
        """Stands in for `claude --resume <id> --bg`: a woken session keeps the name it was saved with."""
        self.woken.append((cwd, session_id))
        return self.registered(cwd, "Prompt test", session_id)

    def registered(self, cwd, name, sid):
        if self.register:
            entry = {"pid": PID, "sessionId": sid, "cwd": cwd, "name": name, "kind": "bg", "status": "idle"}
            (self.live / f"{name}.json").write_text(json.dumps({**entry, **self.entry}))
        return self.note


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "home"
    (root / "billing").mkdir(parents=True)
    live = tmp_path / "live"
    live.mkdir()
    return root, live


def bridge(tmp_path, world, starter):
    root, live = world
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    return build_server(
        m,
        conversations=lambda **k: [],
        live_dir=live,
        start_background=starter,
        start_seconds=1.0,
        find_program=lambda n: None,
    )


async def test_a_started_session_runs_in_the_background_and_is_listed_at_once(tmp_path, world):
    starter = Starter(world[1])
    async with Client(bridge(tmp_path, world, starter)) as c:
        out = (await c.call_tool("start_active_session", {"folder": "billing", "name": "invoices"})).structured_content
        listed = (await c.call_tool("list_active_sessions", {})).structured_content
    assert out["started_in"] == "background" and "note" not in out
    assert {k: out[k] for k in ("name", "claude_session_id", "folder", "status")} == {
        "name": "invoices",
        "claude_session_id": "sid-invoices",
        "folder": "billing",
        "status": "idle",
    }
    assert starter.calls == [(str(world[0] / "billing"), "invoices")]
    assert "invoices" in {r["name"] for r in listed["sessions"]}


async def test_without_a_name_one_is_made_from_the_folder_and_never_clashes(tmp_path, world):
    starter = Starter(world[1])
    async with Client(bridge(tmp_path, world, starter)) as c:
        first = (await c.call_tool("start_active_session", {"folder": "billing"})).structured_content
        second = (await c.call_tool("start_active_session", {"folder": "billing"})).structured_content
    assert first["name"].startswith("billing-") and second["name"].startswith("billing-")
    assert second["name"] != first["name"]  # no name given: a new session each time


async def test_a_folder_outside_the_root_is_refused(tmp_path, world):
    async with Client(bridge(tmp_path, world, Starter(world[1]))) as c:
        bad = await c.call_tool("start_active_session", {"folder": "/etc"})
    assert bad.is_error and ": folder must be under " in bad.content[0].text


async def test_a_session_that_never_appears_is_reported_not_promised(tmp_path, world):
    async with Client(bridge(tmp_path, world, Starter(world[1], register=False))) as c:
        bad = await c.call_tool("start_active_session", {"folder": "billing", "name": "ghost"})
    assert bad.is_error and "did not appear" in bad.content[0].text


async def test_a_session_started_without_a_model_runs_on_opus(tmp_path, world):
    # Claude Code's own default on the owner's account is Fable; the owner wants Opus.
    starter = Starter(world[1])
    async with Client(bridge(tmp_path, world, starter)) as c:
        await c.call_tool("start_active_session", {"folder": "billing", "name": "plain"})
    assert starter.options[0]["model"] == "opus"


async def test_a_chosen_model_comes_with_a_note_that_it_is_only_checked_at_the_first_reply(tmp_path, world):
    async with Client(bridge(tmp_path, world, Starter(world[1]))) as c:
        deep = (await c.call_tool("start_active_session", {"folder": "billing", "model": "opus"})).structured_content
        plain = (await c.call_tool("start_active_session", {"folder": "billing"})).structured_content
    assert "first reply" in deep["note"]
    assert "note" not in plain


OLD = "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"


async def no_background_sessions():
    return set()


def resuming(tmp_path, world, starter, grant=None, background_sessions=no_background_sessions, **options):
    from test_server import FakeConversations, conversation

    from claude_voice.session_modes import Grant

    root, live = world
    m = SessionManager(
        Store(tmp_path / "b.db"),
        project_root=root,
        client_factory=FakeClaude(),
        grant_for=lambda sid, cwd, new_mode: grant or Grant(new_mode),
    )
    convs = FakeConversations([conversation(OLD, str(root / "billing"), "Invoice export")])
    return build_server(
        m,
        conversations=convs,
        live_dir=live,
        start_background=starter,
        wake_background=starter.wake,
        start_seconds=1.0,
        background_sessions=background_sessions,
        **options,
    )


async def test_a_former_background_session_is_woken_in_place_with_its_saved_options(tmp_path, world):
    # Given any other flag, Claude Code would start a copy of it rather than continue it.
    async def known():
        return {OLD}

    starter = Starter(world[1], note="note: woke session 0f1e2d3c with its saved options (--name).")
    async with Client(resuming(tmp_path, world, starter, background_sessions=known)) as c:
        out = (await c.call_tool("attach_conversation", {"claude_session_id": OLD})).structured_content
    assert starter.woken == [(str(world[0] / "billing"), OLD)] and starter.options == []
    assert out["name"] == "Prompt test" and out["claude_session_id"] == OLD and "copied_from" not in out
    assert out["claude_code_note"].startswith("note: woke session")


async def test_a_conversation_still_running_is_not_started_again(tmp_path, world):
    # Seen 2026-11-13: an archived session still ran in the background; waking it made Claude Code
    # start a copy under the same name, and messages to that name then reached neither.
    async def known():
        return {OLD}

    root, live = world
    (live / "old.json").write_text(
        json.dumps(
            {
                "pid": PID,
                "sessionId": OLD,
                "cwd": str(root / "billing"),
                "name": "Old one",
                "kind": "bg",
                "status": "idle",
            }
        )
    )
    starter = Starter(live)
    async with Client(resuming(tmp_path, world, starter, background_sessions=known)) as c:
        out = (await c.call_tool("attach_conversation", {"claude_session_id": OLD})).structured_content
    assert starter.calls == []
    assert out["already_running"] is True and out["name"] == "Old one" and out["claude_session_id"] == OLD
    assert "copied_from" not in out


async def test_a_message_to_a_session_claude_code_closed_for_low_memory_resumes_it_first(tmp_path, world):
    # Seen 2026-11-13: Claude Code closed eight idle sessions to free memory; their conversations stay.
    async def known():
        return {OLD}

    delivered = []

    async def deliver(name, text):
        delivered.append(name)
        return {"delivered": True, "detail": ""}

    root, live = world
    log = tmp_path / "daemon.log"
    log.write_text(f"[2026-11-13T00:19:50.365Z] [bg] bg retire {OLD[:8]}: settled, idle 2h [low memory]\n")
    starter = Starter(live)
    server = resuming(tmp_path, world, starter, background_sessions=known, deliver=deliver, daemon_log=log)
    Store(tmp_path / "b.db").record_started(OLD, "Prompt test", str(root / "billing"))
    async with Client(server) as c:
        out = (
            await c.call_tool("message_active_session", {"session": "Prompt test", "message": "Hi"})
        ).structured_content
    assert [sid for _, sid in starter.woken] == [OLD] and starter.options == []  # woken in place, not copied
    assert delivered == ["Prompt test"] and out["delivered"] is True
    assert "low memory" in out["resumed"]


async def test_a_session_not_running_is_found_by_its_name_the_way_a_running_one_is(tmp_path, world):
    # Seen 2026-11-14 (ledger F88): a session named "bridge stop keeps session listed", closed for low
    # memory, messaged by its name, got "No running
    # session"; its id resumed it. Said aloud, the capitals may differ too.
    async def known():
        return {OLD}

    delivered = []

    async def deliver(name, text):
        delivered.append(name)
        return {"delivered": True, "detail": ""}

    root, live = world
    log = tmp_path / "daemon.log"
    log.write_text(f"[2026-11-14T09:39:50.365Z] [bg] bg retire {OLD[:8]}: settled, idle 2h [low memory]\n")
    starter = Starter(live)
    jobs = tmp_path / "jobs"  # not this PC's own
    jobs.mkdir()
    server = resuming(
        tmp_path, world, starter, background_sessions=known, deliver=deliver, daemon_log=log, jobs_dir=jobs
    )
    Store(tmp_path / "b.db").record_started(OLD, "Prompt test", str(root / "billing"))
    async with Client(server) as c:
        out = (
            await c.call_tool("message_active_session", {"session": "prompt test", "message": "Hi"})
        ).structured_content
    assert [sid for _, sid in starter.woken] == [OLD]
    assert len(delivered) == 1 and out["delivered"] is True and "low memory" in out["resumed"]


async def test_a_message_to_a_session_claude_code_closed_while_idle_resumes_it_too(tmp_path, world):
    # Seen 2026-11-14: Claude Code closes a Remote Control session 8 hours after it last worked,
    # memory or not, and the log line then has no "[low memory]".
    async def known():
        return {OLD}

    async def deliver(name, text):
        return {"delivered": True, "detail": ""}

    root, live = world
    log = tmp_path / "daemon.log"
    log.write_text(f"[2026-11-15T10:54:58.513Z] [bg] bg retire {OLD[:8]}: settled, idle 10h\n")
    starter = Starter(live)
    server = resuming(tmp_path, world, starter, background_sessions=known, deliver=deliver, daemon_log=log)
    Store(tmp_path / "b.db").record_started(OLD, "Prompt test", str(root / "billing"))
    async with Client(server) as c:
        out = (
            await c.call_tool("message_active_session", {"session": "Prompt test", "message": "Hi"})
        ).structured_content
    assert [sid for _, sid in starter.woken] == [OLD]
    assert "closed by Claude Code while idle" in out["resumed"]


async def test_a_message_to_a_session_left_down_while_parked_resumes_it_too(tmp_path, world):
    # Seen 2026-11-21: after a restart the bridge leaves a parked session down; the Claude app still
    # lists it, and a message to it is a sign the owner wants it back (owner, 2026-11-21).
    from claude_voice import parking

    async def known():
        return {OLD}

    async def deliver(name, text):
        return {"delivered": True, "detail": ""}

    root, live = world
    store = Store(tmp_path / "b.db")
    store.record_started(OLD, "Prompt test", str(root / "billing"))
    parking.leave_down(store, OLD, "2026-11-21T08:00:00.000Z", {})
    starter = Starter(live)
    server = resuming(
        tmp_path, world, starter, background_sessions=known, deliver=deliver, daemon_log=tmp_path / "none.log"
    )
    async with Client(server) as c:
        out = (
            await c.call_tool("message_active_session", {"session": "Prompt test", "message": "Hi"})
        ).structured_content
    assert [sid for _, sid in starter.woken] == [OLD]
    assert "closed at the PC's shutdown, while parked" in out["resumed"]


async def test_a_background_session_the_owner_started_is_known_from_its_job_record_too(tmp_path, world):
    # The owner started it, so the bridge has no record of it: Claude Code's job record names it.
    async def known():
        return {OLD}

    async def deliver(name, text):
        return {"delivered": True, "detail": ""}

    root, live = world
    log = tmp_path / "daemon.log"
    log.write_text(f"[2026-11-15T10:54:58.513Z] [bg] bg retire {OLD[:8]}: settled, idle 10h\n")
    jobs = tmp_path / "jobs"
    (jobs / OLD[:8]).mkdir(parents=True)
    record = {
        "sessionId": OLD,
        "name": "Owner's own",
        "cwd": str(root / "billing"),
        "updatedAt": "2026-11-15T01:00:00Z",
    }
    (jobs / OLD[:8] / "state.json").write_text(json.dumps(record), encoding="utf-8")
    starter = Starter(live)
    server = resuming(
        tmp_path, world, starter, background_sessions=known, deliver=deliver, daemon_log=log, jobs_dir=jobs
    )
    async with Client(server) as c:
        out = (
            await c.call_tool("message_active_session", {"session": "Owner's own", "message": "Hi"})
        ).structured_content
    assert [sid for _, sid in starter.woken] == [OLD]
    assert "closed by Claude Code while idle" in out["resumed"]


async def test_a_message_to_a_session_stopped_on_purpose_does_not_bring_it_back(tmp_path, world):
    async def known():
        return {OLD}

    root, live = world
    starter = Starter(live)
    server = resuming(tmp_path, world, starter, background_sessions=known, daemon_log=tmp_path / "none.log")
    Store(tmp_path / "b.db").record_started(OLD, "Prompt test", str(root / "billing"))
    async with Client(server) as c:
        out = await c.call_tool("message_active_session", {"session": "Prompt test", "message": "Hi"})
    assert out.is_error and starter.calls == []


async def test_what_claude_code_notes_at_the_start_comes_back(tmp_path, world):
    starter = Starter(world[1], note="Some banner\nnote: something to know\nbackgrounded")
    async with Client(bridge(tmp_path, world, starter)) as c:
        out = (await c.call_tool("start_active_session", {"folder": "billing", "name": "noted"})).structured_content
    async with Client(bridge(tmp_path, world, Starter(world[1]))) as c:
        plain = (await c.call_tool("start_active_session", {"folder": "billing", "name": "quiet"})).structured_content
    assert out["claude_code_note"] == "note: something to know"
    assert "claude_code_note" not in plain


async def test_an_old_conversation_resumes_as_a_background_session_with_its_own_permissions(tmp_path, world):
    from claude_voice.session_modes import Grant

    starter = Starter(world[1])
    grant = Grant("acceptEdits", (r"D:\work\shared",))
    async with Client(resuming(tmp_path, world, starter, grant)) as c:
        out = (await c.call_tool("attach_conversation", {"claude_session_id": OLD})).structured_content
        listed = (await c.call_tool("list_active_sessions", {})).structured_content
    assert starter.calls == [(str(world[0] / "billing"), "Invoice export")]
    assert starter.options[0]["resume"] == OLD
    assert starter.options[0]["permission_mode"] == "acceptEdits"
    assert starter.options[0]["add_dirs"] == (r"D:\work\shared",)
    assert out["claude_session_id"] == OLD and out["name"] == "Invoice export" and "copied_from" not in out
    assert "Invoice export" in {r["name"] for r in listed["sessions"]}


async def test_a_conversation_claude_code_could_not_continue_in_place_is_reported_as_a_copy(tmp_path, world):
    async with Client(resuming(tmp_path, world, Starter(world[1], copies=True))) as c:
        out = (await c.call_tool("attach_conversation", {"claude_session_id": OLD})).structured_content
    assert out["copied_from"] == OLD and out["claude_session_id"] == "sid-Invoice export"


async def test_a_resumed_session_can_be_stopped_like_one_the_bridge_started(tmp_path, world):
    stopper = Stopper(world[1])
    root, live = world
    from test_server import FakeConversations, conversation

    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    convs = FakeConversations([conversation(OLD, str(root / "billing"), "Invoice export")])
    srv = build_server(
        m,
        conversations=convs,
        live_dir=live,
        start_background=Starter(live),
        stop_background=stopper,
        start_seconds=1.0,
    )
    async with Client(srv) as c:
        await c.call_tool("attach_conversation", {"claude_session_id": OLD})
        out = (await c.call_tool("stop_active_session", {"name": "Invoice export"})).structured_content
    assert out["status"] == "stopped" and stopper.calls == [OLD]


class Stopper:
    """Stands in for `claude stop`: records the call and removes the session's registry entry."""

    def __init__(self, live):
        self.live = live
        self.calls = []

    async def __call__(self, session_id):
        self.calls.append(session_id)
        for f in self.live.glob("*.json"):
            if json.loads(f.read_text(encoding="utf-8")).get("sessionId") == session_id:
                f.unlink()


def bridge_with_stop(tmp_path, world, starter, stopper, windows=None, transcript=None):
    root, live = world
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    return build_server(
        m,
        conversations=lambda **k: [],
        live_dir=live,
        start_background=starter,
        find_program=lambda name: None,  # no terminal here: sessions start in the background
        stop_background=stopper,
        stop_window=windows or Windows(live),  # never the real one: it would end this test's process
        start_seconds=1.0,
        read_transcript=(lambda sid, cwd: transcript) if transcript is not None else None,
    )


async def test_a_session_the_bridge_started_can_be_stopped_and_is_gone_after(tmp_path, world):
    stopper = Stopper(world[1])
    async with Client(bridge_with_stop(tmp_path, world, Starter(world[1]), stopper)) as c:
        await c.call_tool("start_active_session", {"folder": "billing", "name": "invoices"})
        out = (await c.call_tool("stop_active_session", {"name": "invoices"})).structured_content
        listed = (await c.call_tool("list_active_sessions", {})).structured_content
        again = (await c.call_tool("stop_active_session", {"name": "invoices"})).structured_content
    assert out == {"name": "invoices", "status": "stopped"}
    assert stopper.calls == ["sid-invoices"]
    assert "invoices" not in {r["name"] for r in listed["sessions"]}
    assert again["status"] == "not_running"


REMOTE = {"bridgeSessionId": "session_017invoices"}  # Claude Code's registry names the session's Remote Control link


async def test_an_idle_background_session_with_remote_control_is_parked_not_stopped(tmp_path, world):
    # Stopped, Claude Code would archive it in claude.ai and it would leave the owner's list in the app;
    # closed by Claude Code itself once at rest, it stays there and can be woken (ADR-CV-107).
    stopper = Stopper(world[1])
    async with Client(bridge_with_stop(tmp_path, world, Starter(world[1], entry=REMOTE), stopper)) as c:
        await c.call_tool("start_active_session", {"folder": "billing", "name": "invoices"})
        out = (await c.call_tool("stop_active_session", {"name": "invoices"})).structured_content
        listed = (await c.call_tool("list_active_sessions", {})).structured_content
    assert out["status"] == "parked" and stopper.calls == []
    # Said for the owner, who asked to stop it and will still see it in the app.
    assert "about 8 hours after it last worked" in out["say"] and "now=true" in out["say"]
    row = next(r for r in listed["sessions"] if r["claude_session_id"] == "sid-invoices")
    assert row["parked"] is True and row["status"] == "idle"


async def test_a_session_stopped_now_is_stopped_at_once_even_when_idle(tmp_path, world):
    stopper = Stopper(world[1])
    async with Client(bridge_with_stop(tmp_path, world, Starter(world[1], entry=REMOTE), stopper)) as c:
        await c.call_tool("start_active_session", {"folder": "billing", "name": "invoices"})
        parked = (await c.call_tool("stop_active_session", {"name": "invoices"})).structured_content
        out = (await c.call_tool("stop_active_session", {"name": "invoices", "now": True})).structured_content
        listed = (await c.call_tool("list_active_sessions", {})).structured_content
    assert parked["status"] == "parked"
    assert out == {"name": "invoices", "status": "stopped"} and stopper.calls == ["sid-invoices"]
    assert "sid-invoices" not in {r["claude_session_id"] for r in listed["sessions"]}


async def test_a_session_at_work_is_stopped_at_once_even_with_remote_control(tmp_path, world):
    # Claude Code never closes a busy session, so parking one would leave it running.
    stopper = Stopper(world[1])
    busy = Starter(world[1], entry={**REMOTE, "status": "busy"})
    async with Client(bridge_with_stop(tmp_path, world, busy, stopper)) as c:
        await c.call_tool("start_active_session", {"folder": "billing", "name": "invoices"})
        out = (await c.call_tool("stop_active_session", {"name": "invoices"})).structured_content
    assert out == {"name": "invoices", "status": "stopped"} and stopper.calls == ["sid-invoices"]


async def test_an_idle_session_with_a_background_task_still_running_is_stopped_at_once(tmp_path, world):
    # Its turn ended, but the task it left running will start another: Claude Code holds back
    # its close while the task runs, so parked, it would go on working.
    stopper = Stopper(world[1])
    task = SimpleNamespace(task="bcw", at=None)  # a task started in the background, not ended
    bridge = bridge_with_stop(tmp_path, world, Starter(world[1], entry=REMOTE), stopper, transcript=[task])
    async with Client(bridge) as c:
        await c.call_tool("start_active_session", {"folder": "billing", "name": "invoices"})
        out = (await c.call_tool("stop_active_session", {"name": "invoices"})).structured_content
    assert out == {"name": "invoices", "status": "stopped"} and stopper.calls == ["sid-invoices"]


async def test_a_parked_mark_goes_once_its_session_is_no_longer_running(tmp_path, world):
    live = world[1]
    async with Client(bridge_with_stop(tmp_path, world, Starter(live, entry=REMOTE), Stopper(live))) as c:
        await c.call_tool("start_active_session", {"folder": "billing", "name": "invoices"})
        await c.call_tool("stop_active_session", {"name": "invoices"})
        parked = Store(tmp_path / "b.db").get_setting("parked")
        (live / "invoices.json").unlink()  # Claude Code closed it
        await c.call_tool("list_active_sessions", {})
    assert set(parked) == {"sid-invoices"}
    assert Store(tmp_path / "b.db").get_setting("parked") == {}


async def test_a_session_shows_as_parked_only_until_it_works_again(tmp_path, world):
    live = world[1]
    async with Client(bridge_with_stop(tmp_path, world, Starter(live, entry=REMOTE), Stopper(live))) as c:
        await c.call_tool("start_active_session", {"folder": "billing", "name": "invoices"})
        await c.call_tool("stop_active_session", {"name": "invoices"})
        entry = live / "invoices.json"
        # The owner wrote to it from the app: it worked, and is at rest again.
        worked = {**json.loads(entry.read_text(encoding="utf-8")), "statusUpdatedAt": 4_102_444_800_000}
        entry.write_text(json.dumps(worked))
        listed = (await c.call_tool("list_active_sessions", {})).structured_content
    row = next(r for r in listed["sessions"] if r["claude_session_id"] == "sid-invoices")
    assert "parked" not in row


def owners_session(live, cwd, name, sid, **extra):
    """A session the owner opened themselves: in the registry, never in the bridge's record of starts."""
    entry = {"pid": PID, "sessionId": sid, "cwd": str(cwd), "name": name, "status": "idle", **extra}
    (live / f"{sid}.json").write_text(json.dumps(entry))


async def test_any_session_in_a_folder_open_to_the_bridge_can_be_stopped_the_owner_s_own_too(tmp_path, world):
    root, live = world
    owners_session(live, root / "billing", "my-terminal", "sid-term", kind="interactive", entrypoint="cli")
    owners_session(live, root / "billing", "my-background", "sid-bg", kind="bg")
    stopper, windows = Stopper(live), Windows(live)
    async with Client(bridge_with_stop(tmp_path, world, Starter(live), stopper, windows)) as c:
        # The background one first: the fake window closer drops every entry with this test's pid.
        bg = (await c.call_tool("stop_active_session", {"name": "my-background"})).structured_content
        term = (await c.call_tool("stop_active_session", {"name": "my-terminal"})).structured_content
    assert term == {"name": "my-terminal", "status": "stopped"} and windows.closed == [PID]
    assert bg == {"name": "my-background", "status": "stopped"} and stopper.calls == ["sid-bg"]


async def test_a_session_in_a_folder_not_open_to_the_bridge_is_not_stopped(tmp_path, world):
    root, live = world
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    owners_session(live, outside, "private", "sid-private", kind="bg")
    stopper, windows = Stopper(live), Windows(live)
    async with Client(bridge_with_stop(tmp_path, world, Starter(live), stopper, windows)) as c:
        out = (await c.call_tool("stop_active_session", {"name": "private"})).structured_content
    assert out == {"name": "private", "status": "folder_not_open"}
    assert stopper.calls == [] and windows.closed == []
    assert (live / "sid-private.json").exists()


async def test_only_a_terminal_or_background_session_is_stopped(tmp_path, world):
    # A program's own session through Claude Code's SDK: ending its process would break that program.
    root, live = world
    owners_session(live, root / "billing", "sdk-agent", "sid-sdk", kind="interactive", entrypoint="sdk-py")
    stopper, windows = Stopper(live), Windows(live)
    async with Client(bridge_with_stop(tmp_path, world, Starter(live), stopper, windows)) as c:
        out = (await c.call_tool("stop_active_session", {"name": "sdk-agent"})).structured_content
    assert out["status"] == "not_stoppable" and "SDK" in out["why"]
    assert stopper.calls == [] and windows.closed == []


async def test_the_record_of_started_sessions_survives_a_restart_of_the_bridge(tmp_path, world):
    stopper = Stopper(world[1])
    async with Client(bridge_with_stop(tmp_path, world, Starter(world[1]), stopper)) as c:
        await c.call_tool("start_active_session", {"folder": "billing", "name": "invoices"})
    async with Client(bridge_with_stop(tmp_path, world, Starter(world[1]), stopper)) as c:  # same database
        out = (await c.call_tool("stop_active_session", {"name": "invoices"})).structured_content
    assert out["status"] == "stopped"


class ModelStarter(Starter):
    async def __call__(self, cwd, name, model=None, effort=None, **how):
        self.models = (model, effort)
        await super().__call__(cwd, name)


async def test_a_session_can_be_started_on_a_chosen_model_and_effort(tmp_path, world):
    starter = ModelStarter(world[1])
    async with Client(bridge(tmp_path, world, starter)) as c:
        out = (
            await c.call_tool("start_active_session", {"folder": "billing", "model": "opus", "effort": "high"})
        ).structured_content
        plain = (await c.call_tool("start_active_session", {"folder": "billing"})).structured_content
    assert starter.models == ("opus", None)  # the second start: Opus, as this fork chose, and Claude Code's effort
    assert "first reply" in out["note"] and "first reply" not in plain.get("note", "")


@pytest.mark.parametrize(("field", "value"), [("model", "opus; rm -rf /"), ("model", ""), ("effort", "extreme")])
async def test_only_a_well_formed_model_name_and_a_known_effort_reach_claude_code(tmp_path, world, field, value):
    starter = ModelStarter(world[1])
    async with Client(bridge(tmp_path, world, starter)) as c:
        bad = await c.call_tool("start_active_session", {"folder": "billing", field: value})
    assert bad.is_error and starter.calls == []


def test_the_background_start_passes_model_and_effort_to_claude_code():
    from claude_voice import transcripts
    from claude_voice.transcripts import background_command

    direct = ["--settings", json.dumps(transcripts.BRIDGE_SETTINGS)]
    assert background_command("claude", "x") == ["claude", "--bg", "--name", "x", *direct]
    assert background_command("claude", "x", model="opus", effort="max") == [
        "claude",
        "--bg",
        "--name",
        "x",
        "--model",
        "opus",
        "--effort",
        "max",
        *direct,
    ]


async def test_starting_the_same_session_twice_gives_back_the_one_already_running(tmp_path, world):
    """A retry after an answer lost on the way must not open a second window."""
    starter = Starter(world[1])
    async with Client(bridge(tmp_path, world, starter)) as c:
        first = (
            await c.call_tool("start_active_session", {"folder": "billing", "name": "invoices"})
        ).structured_content
        again = (
            await c.call_tool("start_active_session", {"folder": "billing", "name": "invoices"})
        ).structured_content
    assert len(starter.calls) == 1
    assert again["claude_session_id"] == first["claude_session_id"] and again["already_running"] is True


class Windows:
    """Stands in for closing a terminal window: records the process and removes its registry entry."""

    def __init__(self, live):
        self.live, self.closed = live, []

    def __call__(self, pid):
        self.closed.append(pid)
        for f in self.live.glob("*.json"):
            if json.loads(f.read_text(encoding="utf-8")).get("pid") == pid:
                f.unlink()


async def test_a_window_the_bridge_opened_can_be_stopped_by_the_bridge(tmp_path, world):
    root, live = world

    async def in_a_window(cwd, name, **how):
        (live / f"{name}.json").write_text(
            json.dumps({"pid": PID, "sessionId": f"sid-{name}", "cwd": cwd, "name": name, "kind": "interactive"})
        )

    windows, stopper = Windows(live), Stopper(live)
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    server = build_server(
        m,
        conversations=lambda **k: [],
        live_dir=live,
        start_background=in_a_window,
        stop_background=stopper,
        stop_window=windows,
        start_seconds=1.0,
        find_program=lambda n: None,
    )
    async with Client(server) as c:
        await c.call_tool("start_active_session", {"folder": "billing", "name": "inv"})
        out = (await c.call_tool("stop_active_session", {"name": "inv"})).structured_content
    assert out["status"] == "stopped" and windows.closed == [PID] and stopper.calls == []


async def test_a_new_folder_is_made_only_when_asked(tmp_path, world):
    root, live = world
    starter = Starter(live)
    async with Client(bridge(tmp_path, world, starter)) as c:
        refused = await c.call_tool("start_active_session", {"folder": "utgifter", "name": "u"})
        made = (
            await c.call_tool("start_active_session", {"folder": "utgifter", "name": "u", "create_folder": True})
        ).structured_content
    assert refused.is_error  # not made by default
    assert (root / "utgifter").is_dir() and made["folder_created"] is True and made["folder"] == "utgifter"


@pytest.mark.parametrize("name", ["../escape", "a/b", "/etc", "..", ".hidden", "x" * 80, "with space"])
async def test_a_new_folder_must_be_a_simple_name_under_the_root(tmp_path, world, name):
    root, live = world
    async with Client(bridge(tmp_path, world, Starter(live))) as c:
        out = await c.call_tool("start_active_session", {"folder": name, "create_folder": True})
    assert out.is_error and "simple name" in out.content[0].text
    assert sorted(p.name for p in root.iterdir()) == ["billing"]


async def test_an_existing_folder_is_used_as_it_is_and_nothing_is_made(tmp_path, world):
    root, live = world
    (root / "billing" / "keep.txt").write_text("mine")
    async with Client(bridge(tmp_path, world, Starter(live))) as c:
        out = (
            await c.call_tool("start_active_session", {"folder": "billing", "name": "b", "create_folder": True})
        ).structured_content
    assert out["folder_created"] is False and (root / "billing" / "keep.txt").read_text() == "mine"


def test_trusting_a_folder_marks_only_that_folder_and_keeps_every_other_setting(tmp_path):
    from claude_voice import transcripts

    config = tmp_path / ".claude.json"
    config.write_text(json.dumps({"theme": "dark", "projects": {"/old": {"hasTrustDialogAccepted": False, "x": 1}}}))
    transcripts.trust_folder(tmp_path / "new", config)
    after = json.loads(config.read_text())
    assert after["theme"] == "dark" and after["projects"]["/old"] == {"hasTrustDialogAccepted": False, "x": 1}
    assert after["projects"][(tmp_path / "new").as_posix()]["hasTrustDialogAccepted"] is True


def test_a_folder_is_trusted_under_the_name_claude_code_looks_it_up_by(tmp_path):
    """Claude Code keys a Windows folder with forward slashes; a backslashed key goes unread."""
    from pathlib import PureWindowsPath

    from claude_voice import transcripts

    config = tmp_path / ".claude.json"
    config.write_text(json.dumps({"projects": {}}))
    transcripts.trust_folder(PureWindowsPath(r"D:\work\new"), config)  # type: ignore[arg-type]
    assert list(json.loads(config.read_text())["projects"]) == ["D:/work/new"]


async def test_only_a_folder_the_bridge_just_made_is_trusted_for_claude_code(tmp_path, world):
    root, live = world
    trusted = []
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    server = build_server(
        m,
        conversations=lambda **k: [],
        live_dir=live,
        start_background=Starter(live),
        start_seconds=1.0,
        find_program=lambda n: None,
        trust_folder=trusted.append,
    )
    async with Client(server) as c:
        await c.call_tool("start_active_session", {"folder": "utgifter", "name": "u", "create_folder": True})
        await c.call_tool("start_active_session", {"folder": "billing", "name": "b", "create_folder": True})
    assert trusted == [root / "utgifter"]  # an existing folder is never trusted on its own


async def test_a_session_that_started_without_its_claude_ai_connectors_is_restarted_in_place(tmp_path, world):
    # Seen 2026-11-13: a session respawned as the PC woke had none of its claude.ai connectors (ledger Q104).
    import asyncio

    from test_connectors import EARLIER, WAKE, at, full_start, log

    root, live = world
    cwd = str(root / "billing")
    started = int(at(WAKE) * 1000) - 600
    entry = {"pid": PID, "sessionId": OLD, "cwd": cwd, "name": "Prompt test", "kind": "bg", "status": "idle"}
    (live / "old.json").write_text(json.dumps({**entry, "startedAt": started}))
    logs = tmp_path / "cache"
    full_start(logs, EARLIER, sid=OLD, cwd=cwd)
    log(logs, "plugin-relay-channel-claude-voice", WAKE, sid=OLD, cwd=cwd)
    stopped = []

    async def stop(sid):
        stopped.append(sid)
        (live / "old.json").unlink()

    async def up():
        return True

    starter = Starter(live)
    Store(tmp_path / "b.db").record_started(OLD, "Prompt test", cwd)
    server = resuming(
        tmp_path,
        world,
        starter,
        stop_background=stop,
        connector_logs=logs,
        connectors_reachable=up,
        watch_interval=0.05,
    )
    async with Client(server):
        for _ in range(100):
            if starter.calls:
                break
            await asyncio.sleep(0.05)
    assert stopped == [OLD]
    assert [sid for _, sid in starter.woken] == [
        OLD
    ] and starter.options == []  # resumed in place, its conversation kept


async def test_a_name_already_taken_elsewhere_gets_a_suffix_rather_than_a_clash(tmp_path, world):
    root, live = world
    (root / "other").mkdir()
    (live / "busy.json").write_text(
        json.dumps(
            {"pid": PID, "sessionId": "sid-old", "cwd": str(root / "other"), "name": "invoices", "status": "idle"}
        )
    )
    starter = Starter(live)
    asked = {"folder": "billing", "name": "invoices", "owner_chose": True}
    async with Client(bridge(tmp_path, world, starter)) as c:
        out = (await c.call_tool("start_active_session", asked)).structured_content
    assert out["name"].startswith("invoices-") and starter.calls == [(str(root / "billing"), out["name"])]


async def test_an_unknown_place_to_start_in_is_refused_before_anything_starts(tmp_path, world):
    starter = Starter(world[1])
    async with Client(bridge(tmp_path, world, starter)) as c:
        bad = await c.call_tool("start_active_session", {"folder": "billing", "start_in": "moon"})
    assert (
        bad.is_error
        and bad.content[0].text.endswith("start_in is one of: terminal, background")
        and starter.calls == []
    )


async def test_a_start_claude_code_refuses_is_reported_with_its_reason(tmp_path, world):
    async def refuses(cwd, name, **how):
        raise OSError("claude not found")

    async with Client(bridge(tmp_path, world, refuses)) as c:
        bad = await c.call_tool("start_active_session", {"folder": "billing", "name": "x"})
    assert bad.is_error and bad.content[0].text.endswith(": Claude Code could not start a session: claude not found")
