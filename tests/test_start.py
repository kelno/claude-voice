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

    async def __call__(self, cwd, name, **options):
        self.calls.append((cwd, name))
        self.options.append(options)
        sid = options.get("resume") if options.get("resume") and not self.copies else f"sid-{name}"
        if options.get("wake"):
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
    return build_server(m, conversations=lambda **k: [], live_dir=live, start_background=starter, start_seconds=1.0)


async def test_a_started_session_runs_in_the_background_and_is_listed_at_once(tmp_path, world):
    starter = Starter(world[1])
    async with Client(bridge(tmp_path, world, starter)) as c:
        out = (await c.call_tool("start_active_session", {"project": "billing", "name": "invoices"})).structured_content
        listed = (await c.call_tool("list_active_sessions", {})).structured_content
    assert out == {"name": "invoices", "claude_session_id": "sid-invoices", "project": "billing", "status": "idle"}
    assert starter.calls == [(str(world[0] / "billing"), "invoices")]
    assert "invoices" in {r["name"] for r in listed["sessions"]}


async def test_without_a_name_one_is_made_from_the_project_and_never_clashes(tmp_path, world):
    starter = Starter(world[1])
    async with Client(bridge(tmp_path, world, starter)) as c:
        first = (await c.call_tool("start_active_session", {"project": "billing"})).structured_content
        second = (
            await c.call_tool("start_active_session", {"project": "billing", "name": first["name"]})
        ).structured_content
    assert first["name"].startswith("billing-")
    assert second["name"] != first["name"] and second["name"].startswith(first["name"])


async def test_a_folder_outside_the_root_is_refused(tmp_path, world):
    async with Client(bridge(tmp_path, world, Starter(world[1]))) as c:
        bad = await c.call_tool("start_active_session", {"project": "/etc"})
    assert bad.is_error and "under" in bad.content[0].text


async def test_a_session_that_never_appears_is_reported_not_promised(tmp_path, world):
    async with Client(bridge(tmp_path, world, Starter(world[1], register=False))) as c:
        bad = await c.call_tool("start_active_session", {"project": "billing", "name": "ghost"})
    assert bad.is_error and "did not appear" in bad.content[0].text


async def test_a_session_can_be_started_on_a_chosen_model_and_effort(tmp_path, world):
    starter = Starter(world[1])
    async with Client(bridge(tmp_path, world, starter)) as c:
        await c.call_tool(
            "start_active_session", {"project": "billing", "name": "deep", "model": "opus", "effort": "max"}
        )
        await c.call_tool("start_active_session", {"project": "billing", "name": "plain"})
    assert [(o["model"], o["effort"]) for o in starter.options] == [("opus", "max"), ("opus", None)]


async def test_a_session_started_without_a_model_runs_on_opus(tmp_path, world):
    # Claude Code's own default on the owner's account is Fable; the owner wants Opus.
    starter = Starter(world[1])
    async with Client(bridge(tmp_path, world, starter)) as c:
        await c.call_tool("start_active_session", {"project": "billing", "name": "plain"})
    assert starter.options[0]["model"] == "opus"


@pytest.mark.parametrize("model", ["--dangerously-skip-permissions", "opus; rm", "", "o" * 200])
async def test_only_a_well_formed_model_name_and_a_known_effort_reach_claude_code(tmp_path, world, model):
    starter = Starter(world[1])
    async with Client(bridge(tmp_path, world, starter)) as c:
        bad = await c.call_tool("start_active_session", {"project": "billing", "model": model})
    assert bad.is_error and "model" in bad.content[0].text
    assert starter.calls == []


async def test_an_unknown_effort_starts_nothing_and_the_error_lists_the_levels(tmp_path, world):
    starter = Starter(world[1])
    async with Client(bridge(tmp_path, world, starter)) as c:
        bad = await c.call_tool("start_active_session", {"project": "billing", "effort": "huge"})
    assert bad.is_error and all(level in bad.content[0].text for level in ("low", "medium", "high", "xhigh", "max"))
    assert starter.calls == []


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


def bridge_with_stop(tmp_path, world, starter, stopper):
    root, live = world
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    return build_server(
        m,
        conversations=lambda **k: [],
        live_dir=live,
        start_background=starter,
        stop_background=stopper,
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
    stopper = Stopper(live)
    async with Client(bridge_with_stop(tmp_path, world, Starter(live), stopper)) as c:
        out = (await c.call_tool("stop_active_session", {"name": "my-terminal"})).structured_content
    assert out == {"name": "my-terminal", "status": "not_started_here"}
    assert stopper.calls == []
    assert (live / "term.json").exists()


async def test_the_record_of_started_sessions_survives_a_restart_of_the_bridge(tmp_path, world):
    stopper = Stopper(world[1])
    async with Client(bridge_with_stop(tmp_path, world, Starter(world[1]), stopper)) as c:
        await c.call_tool("start_active_session", {"project": "billing", "name": "invoices"})
    async with Client(bridge_with_stop(tmp_path, world, Starter(world[1]), stopper)) as c:  # same database
        out = (await c.call_tool("stop_active_session", {"name": "invoices"})).structured_content
    assert out["status"] == "stopped"
