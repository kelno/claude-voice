"""Starting a background Claude Code session that the voice can talk to at once."""

import json
import os

import pytest
from fakes import FakeClaude
from mcp.client import Client

from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

PID = os.getpid()


class Starter:
    """Stands in for `claude --bg`: records the call and registers the session like Claude Code does."""

    def __init__(self, live, register=True, copies=False, note=None):
        self.live = live
        self.register = register
        self.copies = copies  # Claude Code couldn't continue the conversation in place
        self.note = note  # what Claude Code prints, e.g. a "note:" line
        self.calls = []
        self.options = []

    async def __call__(self, cwd, name, **how):
        self.calls.append((cwd, name))
        self.options.append(how)
        sid = how.get("resume") if how.get("resume") and not self.copies else f"sid-{name}"
        if how.get("wake"):
            name = "Prompt test"  # a woken session keeps the name it was saved with
        if self.register:
            (self.live / f"{name}.json").write_text(
                json.dumps({"pid": PID, "sessionId": sid, "cwd": cwd, "name": name, "kind": "bg", "status": "idle"})
            )
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
        out = (await c.call_tool("start_active_session", {"project": "billing", "name": "invoices"})).structured_content
        listed = (await c.call_tool("list_active_sessions", {})).structured_content
    assert out["started_in"] == "background" and "note" not in out
    assert {k: out[k] for k in ("name", "claude_session_id", "project", "status")} == {
        "name": "invoices",
        "claude_session_id": "sid-invoices",
        "project": "billing",
        "status": "idle",
    }
    assert starter.calls == [(str(world[0] / "billing"), "invoices")]
    assert "invoices" in {r["name"] for r in listed["sessions"]}


async def test_without_a_name_one_is_made_from_the_project_and_never_clashes(tmp_path, world):
    starter = Starter(world[1])
    async with Client(bridge(tmp_path, world, starter)) as c:
        first = (await c.call_tool("start_active_session", {"project": "billing"})).structured_content
        second = (await c.call_tool("start_active_session", {"project": "billing"})).structured_content
    assert first["name"].startswith("billing-") and second["name"].startswith("billing-")
    assert second["name"] != first["name"]  # no name given: a new session each time


async def test_a_folder_outside_the_root_is_refused(tmp_path, world):
    async with Client(bridge(tmp_path, world, Starter(world[1]))) as c:
        bad = await c.call_tool("start_active_session", {"project": "/etc"})
    assert bad.is_error and "under" in bad.content[0].text


async def test_a_session_that_never_appears_is_reported_not_promised(tmp_path, world):
    async with Client(bridge(tmp_path, world, Starter(world[1], register=False))) as c:
        bad = await c.call_tool("start_active_session", {"project": "billing", "name": "ghost"})
    assert bad.is_error and "did not appear" in bad.content[0].text


async def test_a_session_started_without_a_model_runs_on_opus(tmp_path, world):
    # Claude Code's own default on the owner's account is Fable; the owner wants Opus.
    starter = Starter(world[1])
    async with Client(bridge(tmp_path, world, starter)) as c:
        await c.call_tool("start_active_session", {"project": "billing", "name": "plain"})
    assert starter.options[0]["model"] == "opus"


async def test_a_chosen_model_comes_with_a_note_that_it_is_only_checked_at_the_first_reply(tmp_path, world):
    async with Client(bridge(tmp_path, world, Starter(world[1]))) as c:
        deep = (await c.call_tool("start_active_session", {"project": "billing", "model": "opus"})).structured_content
        plain = (await c.call_tool("start_active_session", {"project": "billing"})).structured_content
    assert "first reply" in deep["note"]
    assert "note" not in plain


OLD = "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"


async def no_background_sessions():
    return set()


def resuming(tmp_path, world, starter, grant=None, background_sessions=no_background_sessions):
    from test_server import FakeConversations, conversation

    from claude_voice.permissions import Grant

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
        start_seconds=1.0,
        background_sessions=background_sessions,
    )


async def test_a_former_background_session_is_woken_in_place_with_its_saved_options(tmp_path, world):
    # Given any other flag, Claude Code would start a copy of it rather than continue it.
    async def known():
        return {OLD}

    starter = Starter(world[1], note="note: woke session 0f1e2d3c with its saved options (--name).")
    async with Client(resuming(tmp_path, world, starter, background_sessions=known)) as c:
        out = (await c.call_tool("attach_conversation", {"claude_session_id": OLD})).structured_content
    assert starter.options == [{"resume": OLD, "wake": True}]
    assert out["name"] == "Prompt test" and out["claude_session_id"] == OLD and "copied_from" not in out
    assert out["claude_code_note"].startswith("note: woke session")


async def test_what_claude_code_notes_at_the_start_comes_back(tmp_path, world):
    starter = Starter(world[1], note="Some banner\nnote: something to know\nbackgrounded")
    async with Client(bridge(tmp_path, world, starter)) as c:
        out = (await c.call_tool("start_active_session", {"project": "billing", "name": "noted"})).structured_content
    async with Client(bridge(tmp_path, world, Starter(world[1]))) as c:
        plain = (await c.call_tool("start_active_session", {"project": "billing", "name": "quiet"})).structured_content
    assert out["claude_code_note"] == "note: something to know"
    assert "claude_code_note" not in plain


async def test_an_old_conversation_resumes_as_a_background_session_with_its_own_permissions(tmp_path, world):
    from claude_voice.permissions import Grant

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


def bridge_with_stop(tmp_path, world, starter, stopper, windows=None):
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
    )


async def test_a_session_the_bridge_started_can_be_stopped_and_is_gone_after(tmp_path, world):
    stopper = Stopper(world[1])
    async with Client(bridge_with_stop(tmp_path, world, Starter(world[1]), stopper)) as c:
        await c.call_tool("start_active_session", {"project": "billing", "name": "invoices"})
        out = (await c.call_tool("stop_active_session", {"name": "invoices"})).structured_content
        listed = (await c.call_tool("list_active_sessions", {})).structured_content
        again = (await c.call_tool("stop_active_session", {"name": "invoices"})).structured_content
    assert out == {"name": "invoices", "status": "stopped"}
    assert stopper.calls == ["sid-invoices"]
    assert "invoices" not in {r["name"] for r in listed["sessions"]}
    assert again["status"] == "not_running"


async def test_only_a_session_the_bridge_started_itself_is_ever_stopped(tmp_path, world):
    root, live = world
    (live / "term.json").write_text(
        json.dumps(
            {
                "pid": PID,
                "sessionId": "sid-term",
                "cwd": str(root / "billing"),
                "name": "my-terminal",
                "kind": "interactive",
            }
        )
    )
    stopper, windows = Stopper(live), Windows(live)
    async with Client(bridge_with_stop(tmp_path, world, Starter(live), stopper, windows)) as c:
        out = (await c.call_tool("stop_active_session", {"name": "my-terminal"})).structured_content
    assert out == {"name": "my-terminal", "status": "not_started_here"}
    assert stopper.calls == [] and windows.closed == []
    assert (live / "term.json").exists()


async def test_the_record_of_started_sessions_survives_a_restart_of_the_bridge(tmp_path, world):
    stopper = Stopper(world[1])
    async with Client(bridge_with_stop(tmp_path, world, Starter(world[1]), stopper)) as c:
        await c.call_tool("start_active_session", {"project": "billing", "name": "invoices"})
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
            await c.call_tool("start_active_session", {"project": "billing", "model": "opus", "effort": "high"})
        ).structured_content
        plain = (await c.call_tool("start_active_session", {"project": "billing"})).structured_content
    assert starter.models == ("opus", None)  # the second start: Opus, as this fork chose, and Claude Code's effort
    assert "first reply" in out["note"] and "first reply" not in plain.get("note", "")


@pytest.mark.parametrize(("field", "value"), [("model", "opus; rm -rf /"), ("model", ""), ("effort", "extreme")])
async def test_only_a_well_formed_model_name_and_a_known_effort_reach_claude_code(tmp_path, world, field, value):
    starter = ModelStarter(world[1])
    async with Client(bridge(tmp_path, world, starter)) as c:
        bad = await c.call_tool("start_active_session", {"project": "billing", field: value})
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
            await c.call_tool("start_active_session", {"project": "billing", "name": "invoices"})
        ).structured_content
        again = (
            await c.call_tool("start_active_session", {"project": "billing", "name": "invoices"})
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
        await c.call_tool("start_active_session", {"project": "billing", "name": "inv"})
        out = (await c.call_tool("stop_active_session", {"name": "inv"})).structured_content
    assert out["status"] == "stopped" and windows.closed == [PID] and stopper.calls == []


async def test_a_new_project_folder_is_made_only_when_asked(tmp_path, world):
    root, live = world
    starter = Starter(live)
    async with Client(bridge(tmp_path, world, starter)) as c:
        refused = await c.call_tool("start_active_session", {"project": "utgifter", "name": "u"})
        made = (
            await c.call_tool("start_active_session", {"project": "utgifter", "name": "u", "create_folder": True})
        ).structured_content
    assert refused.is_error  # not made by default
    assert (root / "utgifter").is_dir() and made["folder_created"] is True and made["project"] == "utgifter"


@pytest.mark.parametrize("name", ["../escape", "a/b", "/etc", "..", ".hidden", "x" * 80, "with space"])
async def test_a_new_folder_must_be_a_simple_name_under_the_root(tmp_path, world, name):
    root, live = world
    async with Client(bridge(tmp_path, world, Starter(live))) as c:
        out = await c.call_tool("start_active_session", {"project": name, "create_folder": True})
    assert out.is_error and "simple name" in out.content[0].text
    assert sorted(p.name for p in root.iterdir()) == ["billing"]


async def test_an_existing_folder_is_used_as_it_is_and_nothing_is_made(tmp_path, world):
    root, live = world
    (root / "billing" / "keep.txt").write_text("mine")
    async with Client(bridge(tmp_path, world, Starter(live))) as c:
        out = (
            await c.call_tool("start_active_session", {"project": "billing", "name": "b", "create_folder": True})
        ).structured_content
    assert out["folder_created"] is False and (root / "billing" / "keep.txt").read_text() == "mine"


def test_trusting_a_folder_marks_only_that_folder_and_keeps_every_other_setting(tmp_path):
    from claude_voice import transcripts

    config = tmp_path / ".claude.json"
    config.write_text(json.dumps({"theme": "dark", "projects": {"/old": {"hasTrustDialogAccepted": False, "x": 1}}}))
    transcripts.trust_folder(tmp_path / "new", config)
    after = json.loads(config.read_text())
    assert after["theme"] == "dark" and after["projects"]["/old"] == {"hasTrustDialogAccepted": False, "x": 1}
    assert after["projects"][str(tmp_path / "new")]["hasTrustDialogAccepted"] is True


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
        await c.call_tool("start_active_session", {"project": "utgifter", "name": "u", "create_folder": True})
        await c.call_tool("start_active_session", {"project": "billing", "name": "b", "create_folder": True})
    assert trusted == [root / "utgifter"]  # an existing folder is never trusted on its own
