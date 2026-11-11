"""rename_session: a running session gets a new name, and its old one still reaches it.

The bridge keeps its own name for any running session. A session the bridge started is renamed in
Claude Code too, through its job record, which a running background session watches and takes its
name from at once, and which waking it again reads; any other keeps its old name in Claude Code."""

import json
import logging
import os

import pytest
from mcp.client import Client

from claude_voice import renames
from claude_voice.server import RELAYED_MESSAGE_NOTE, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

SID = "89bb4528-3840-4006-864a-5aa9930e26e0"
FLAGS = ["--name", "math-quest", "--remote-control", "--effort", "low"]


def words(sent):
    """What was sent, without the note the fork's bridge adds after a relayed message."""
    return [(name, text.split("\n\n" + RELAYED_MESSAGE_NOTE)[0]) for name, text in sent]


class Recorder:
    def __init__(self):
        self.sent = []

    async def __call__(self, name, text):
        self.sent.append((name, text))
        return {"delivered": True}


@pytest.fixture
def root(tmp_path):
    (tmp_path / "src" / "math-quest").mkdir(parents=True)
    return tmp_path / "src"


def write(live, pid, sid, name, root, status="idle", kind="interactive"):
    record = {"pid": pid, "sessionId": sid, "cwd": str(root / "math-quest"), "name": name, "status": status}
    (live / f"{pid}.json").write_text(json.dumps({**record, "kind": kind, "updatedAt": 0}), encoding="utf-8")


@pytest.fixture
def live(tmp_path, root):
    d = tmp_path / "live"
    d.mkdir()
    write(d, os.getpid(), "mq-id", "math-quest", root)
    return d


@pytest.fixture
def jobs(tmp_path):
    return tmp_path / "jobs"


def job_record(jobs, sid=SID, name="math-quest"):
    """Claude Code's job record for a background session."""
    path = jobs / sid[:8] / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"state": "idle", "name": name, "nameSource": "user", "respawnFlags": list(FLAGS), "sessionId": sid}
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return path


def started(tmp_path, live, root, jobs, status="idle"):
    """A background session the bridge started, with its job record; the record's path."""
    write(live, os.getpid(), SID, "math-quest", root, status=status, kind="bg")
    Store(tmp_path / "b.db").record_started(SID, "math-quest", str(root / "math-quest"))
    return job_record(jobs)


def server(tmp_path, root, live, jobs, deliver=None):
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root)
    return build_server(
        m,
        conversations=lambda directory=None, limit=None: [],
        live_dir=live,
        deliver=deliver or Recorder(),
        read_transcript=lambda sid, directory: [],
        jobs_dir=jobs,
    )


async def call(c, name, **args):
    res = await c.call_tool(name, args)
    assert not res.is_error, res.content
    return res.structured_content


async def names(c):
    return [s["name"] for s in (await call(c, "list_active_sessions"))["sessions"]]


async def test_a_renamed_session_is_listed_by_its_new_name(tmp_path, root, live, jobs):
    async with Client(server(tmp_path, root, live, jobs)) as c:
        out = await call(c, "rename_session", session="math-quest", new_name="math-quest-curriculum")
        assert await names(c) == ["math-quest-curriculum"]
        fleet = await call(c, "fleet_recap")
        tree = await call(c, "session_tree")
    assert out["name"] == "math-quest-curriculum" and out["was"] == "math-quest"
    assert [s["name"] for s in fleet["sessions"]] == ["math-quest-curriculum"]
    assert "math-quest-curriculum" in [n["name"] for n in tree["nodes"]]


async def test_a_message_to_an_old_name_reaches_the_renamed_session(tmp_path, root, live, jobs):
    deliver = Recorder()
    async with Client(server(tmp_path, root, live, jobs, deliver=deliver)) as c:
        await call(c, "rename_session", session="math-quest", new_name="math-quest-curriculum")
        await call(c, "message_active_session", session="math-quest", message="old")
        await call(c, "message_active_session", session="math-quest-curriculum", message="new")
    # Claude Code still answers to the old name, so that is where it goes.
    assert words(deliver.sent) == [("math-quest", "old"), ("math-quest", "new")]


async def test_a_session_the_bridge_started_gets_its_new_name_in_its_job_record(tmp_path, root, live, jobs):
    record = started(tmp_path, live, root, jobs)
    async with Client(server(tmp_path, root, live, jobs)) as c:
        out = await call(c, "rename_session", session="math-quest", new_name="math-quest-curriculum")
    assert out["claude_code"] == "renamed"
    saved = json.loads(record.read_text(encoding="utf-8"))
    assert (saved["name"], saved["nameSource"]) == ("math-quest-curriculum", "user")
    # Waking the session again starts it with these flags, so the name lasts past a stop.
    assert saved["respawnFlags"] == ["--name", "math-quest-curriculum", "--remote-control", "--effort", "low"]
    assert saved["state"] == "idle" and saved["sessionId"] == SID
    assert Store(tmp_path / "b.db").started_session("math-quest-curriculum")["claude_session_id"] == SID


async def test_a_message_to_an_old_name_arrives_once_claude_code_has_the_new_one(tmp_path, root, live, jobs):
    started(tmp_path, live, root, jobs)
    deliver = Recorder()
    async with Client(server(tmp_path, root, live, jobs, deliver=deliver)) as c:
        await call(c, "rename_session", session="math-quest", new_name="math-quest-curriculum")
        # The running session reads its job record and takes the new name, as Claude Code does.
        write(live, os.getpid(), SID, "math-quest-curriculum", root, kind="bg")
        await call(c, "message_active_session", session="math-quest", message="old")
        assert await names(c) == ["math-quest-curriculum"]
    assert words(deliver.sent) == [("math-quest-curriculum", "old")]


async def test_only_a_session_the_bridge_started_is_renamed_in_claude_code_itself(tmp_path, root, live, jobs):
    write(live, os.getpid(), SID, "math-quest", root, kind="bg")  # running, but not started here
    record = job_record(jobs)
    async with Client(server(tmp_path, root, live, jobs)) as c:
        out = await call(c, "rename_session", session="math-quest", new_name="mq")
        assert await names(c) == ["mq"]
    assert out["claude_code"] == "bridge_only"
    assert "bridge's only" in out["note"] and "desktop app" in out["note"] and "/rename mq" in out["note"]
    assert json.loads(record.read_text(encoding="utf-8"))["name"] == "math-quest"


async def test_a_session_name_may_be_any_text_on_one_line(tmp_path, root, live, jobs):
    # The owner's call: renames take what starts take, spaces and colons included; upstream's
    # narrower names were for typing into tmux, which the fork never does.
    record = started(tmp_path, live, root, jobs)
    async with Client(server(tmp_path, root, live, jobs)) as c:
        out = await call(c, "rename_session", session="math-quest", new_name="  Billing:\n export  fix ")
        assert await names(c) == ["Billing: export fix"]
    assert out["name"] == "Billing: export fix" and out["claude_code"] == "renamed"
    assert json.loads(record.read_text(encoding="utf-8"))["name"] == "Billing: export fix"


@pytest.mark.parametrize("bad", ["", "   ", "\n\t"])
async def test_a_session_name_needs_at_least_one_visible_character(tmp_path, root, live, jobs, bad):
    async with Client(server(tmp_path, root, live, jobs)) as c:
        res = await c.call_tool("rename_session", {"session": "math-quest", "new_name": bad})
        assert res.is_error and "visible" in res.content[0].text
        assert await names(c) == ["math-quest"]


async def test_a_new_name_already_in_use_is_refused(tmp_path, root, live, jobs):
    write(live, os.getppid(), "other-id", "math-quest-manager", root)  # a process that is alive everywhere
    async with Client(server(tmp_path, root, live, jobs)) as c:
        res = await c.call_tool("rename_session", {"session": "math-quest", "new_name": "math-quest-manager"})
        assert res.is_error and "in use" in res.content[0].text
        await call(c, "rename_session", session="math-quest-manager", new_name="mq-manager")
        # An old name still answers, so it is taken too.
        res = await c.call_tool("rename_session", {"session": "math-quest", "new_name": "math-quest-manager"})
        assert res.is_error and "in use" in res.content[0].text


async def test_the_job_record_of_a_busy_session_is_never_written(tmp_path, root, live, jobs):
    # Claude Code rewrites the job record often during a turn; a change then could be lost.
    record = started(tmp_path, live, root, jobs, status="busy")
    async with Client(server(tmp_path, root, live, jobs)) as c:
        out = await call(c, "rename_session", session="math-quest", new_name="math-quest-curriculum")
        assert await names(c) == ["math-quest-curriculum"]
        assert json.loads(record.read_text(encoding="utf-8"))["name"] == "math-quest"
        assert out["claude_code"] == "busy" and "again" in out["note"]
        # Asked again once it is idle, Claude Code gets the name too.
        write(live, os.getpid(), SID, "math-quest", root, kind="bg")
        out = await call(c, "rename_session", session="math-quest-curriculum", new_name="math-quest-curriculum")
    assert out["claude_code"] == "renamed"
    assert json.loads(record.read_text(encoding="utf-8"))["name"] == "math-quest-curriculum"


async def test_a_session_without_a_job_record_is_renamed_in_the_bridge_only(tmp_path, root, live, jobs, caplog):
    caplog.set_level(logging.INFO, logger="claude_voice")
    started(tmp_path, live, root, jobs).unlink()
    async with Client(server(tmp_path, root, live, jobs)) as c:
        out = await call(c, "rename_session", session="math-quest", new_name="mq")
        assert await names(c) == ["mq"]
    assert out["claude_code"] == "bridge_only" and "job record" in out["note"]
    assert any("renamed math-quest -> mq" in r.getMessage() for r in caplog.records)


async def test_renaming_an_unknown_session_is_refused(tmp_path, root, live, jobs):
    async with Client(server(tmp_path, root, live, jobs)) as c:
        res = await c.call_tool("rename_session", {"session": "nope", "new_name": "x"})
    assert res.is_error and "list_active_sessions" in res.content[0].text


async def test_a_name_set_in_claude_code_afterwards_wins(tmp_path, root, live, jobs):
    async with Client(server(tmp_path, root, live, jobs)) as c:
        await call(c, "rename_session", session="math-quest", new_name="mq")
        write(live, os.getpid(), "mq-id", "typed-by-roy", root)
        assert await names(c) == ["typed-by-roy"]


def test_a_session_name_is_found_whatever_its_capitals():
    # By voice, a name comes back with the capitals the transcription chose.
    rows = [{"name": "Test engine session", "sessionId": "a"}]
    assert renames.find(rows, "Test Engine Session")["sessionId"] == "a"
    renamed = {"name": "x", "sessionId": "b", "aliases": ["Old Name"]}
    assert renames.find([*rows, renamed], "old name")["sessionId"] == "b"


def test_names_that_differ_only_by_capitals_are_all_refused_unless_one_is_exact():
    rows = [{"name": "Billing", "sessionId": "a"}, {"name": "billing", "sessionId": "b"}]
    with pytest.raises(renames.Ambiguous) as refused:
        renames.find(rows, "BILLING")
    assert refused.value.names == ["Billing", "billing"]
    assert renames.find(rows, "billing")["sessionId"] == "b"
