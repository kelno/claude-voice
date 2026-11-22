"""The bridge keeps its sessions in step with the Claude app (app_sync.py): a session the owner archived
in the app is stopped at once, and a desktop session left idle is copied to the bridge."""

import json

from claude_voice import app_sync

SID = "4a145eb2-46a0-41cc-8c09-e5b5ddad9498"
DESK = "09c3f956-5a93-4300-827e-fc6b588323ee"
NOW = 1_800_000_000.0
ARCHIVED = "Remote Control disconnected — this session was ended or archived from another device or app (code 4090)"
DELETED = (
    "Remote Control disconnected — the server no longer reports this session — it may have been deleted "
    "from another device or app (code 4090)"
)


def background(sid=SID, link=None, status="idle", started=NOW - 3600):
    return {
        "sessionId": sid,
        "name": "Qobuz in the Navidrome player",
        "kind": "bg",
        "entrypoint": "cli",
        "status": status,
        "startedAt": started * 1000,
        "statusUpdatedAt": (NOW - 600) * 1000,
        "bridgeSessionId": link,
    }


def desktop(sid=DESK, status="idle", updated=NOW - 600, name="Test session"):
    return {
        "sessionId": sid,
        "name": name,
        "kind": "interactive",
        "entrypoint": "claude-desktop",
        "status": status,
        "startedAt": (NOW - 3600) * 1000,
        "statusUpdatedAt": updated * 1000,
        "bridgeSessionId": "session_x",
    }


ON_RC = {"respawnFlags": ["--name", "Qobuz in the Navidrome player", "--remote-control"]}


class World:
    """The bridge as the keeper sees it: running sessions, which were archived, and what it can do."""

    def __init__(self, rows, records=None, archived=()):
        self.rows = rows
        self.records = {SID: ON_RC} if records is None else records
        self.archived_ids = set(archived)
        self.now = NOW
        self.stopped, self.copied, self.recorded = [], [], []
        self.copies: dict = {}
        self.fail_copy = self.fail_stop = False

    def keeper(self):
        async def stop(d):
            if self.fail_stop:
                raise RuntimeError("claude stop failed")
            self.stopped.append(d["sessionId"])

        async def copy(d):
            if self.fail_copy:
                raise RuntimeError("no such folder")
            self.copied.append(d["sessionId"])
            return {"claude_session_id": "copy-of-" + d["sessionId"][:8], "name": d["name"] + "-ab12"}

        return app_sync.Keeper(
            live=lambda: self.rows,
            job_record=lambda sid: self.records.get(sid),
            archived=lambda d: d["sessionId"] in self.archived_ids,
            stop=stop,
            copy=copy,
            copies=lambda: self.copies,
            remember_copy=lambda sid, info: self.copies.__setitem__(sid, info),
            record=lambda name, kind, text: self.recorded.append((kind, text)),
            clock=lambda: self.now,
        )


# -- a session archived in the app ------------------------------------------------------------------


async def test_a_session_archived_in_the_claude_app_is_stopped_at_once():
    world = World([background()], archived=[SID])
    await world.keeper().step()
    assert world.stopped == [SID]
    assert world.recorded[0][0] == "stopped_archived"


async def test_a_busy_session_archived_in_the_app_is_stopped_too():
    world = World([background(status="busy")], archived=[SID])
    await world.keeper().step()
    assert world.stopped == [SID]


async def test_a_session_with_its_link_is_left_running():
    world = World([background(link="session_017aWUqg7")], archived=[SID])
    await world.keeper().step()
    assert world.stopped == []


async def test_a_session_without_its_link_but_not_archived_is_left_running():
    world = World([background()])
    await world.keeper().step()
    assert world.stopped == []


async def test_a_stop_that_fails_is_recorded_once_for_that_start():
    world = World([background()], archived=[SID])
    world.fail_stop = True
    keeper = world.keeper()
    await keeper.step()
    await keeper.step()
    assert [k for k, _ in world.recorded] == ["stop_failed"]


def transcript(tmp_path, *lines):
    path = tmp_path / f"{SID}.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return path


def said(content, at):
    return {"type": "system", "subtype": "informational", "content": content, "timestamp": at}


def test_an_archive_since_the_session_started_counts(tmp_path):
    path = transcript(tmp_path, {"type": "user"}, said(ARCHIVED, "2026-11-22T18:12:32.706Z"))
    started = app_sync._ms("2026-11-22T17:00:00.000Z")
    assert app_sync.archived_since(path, started)


def test_only_an_archive_since_the_session_started_counts(tmp_path):
    path = transcript(tmp_path, said(ARCHIVED, "2026-11-22T17:14:35.966Z"))
    started = app_sync._ms("2026-11-22T17:37:00.000Z")  # respawned after it
    assert not app_sync.archived_since(path, started)


def test_a_deletion_in_the_app_counts_as_an_archive(tmp_path):
    path = transcript(tmp_path, said(DELETED, "2026-11-22T18:12:32.706Z"))
    assert app_sync.archived_since(path, app_sync._ms("2026-11-22T17:00:00.000Z"))


def test_other_disconnects_and_a_missing_transcript_tell_nothing(tmp_path):
    path = transcript(tmp_path, said("Remote Control disconnected — network error", "2026-11-22T18:12:32.706Z"))
    assert not app_sync.archived_since(path, 0)
    assert not app_sync.archived_since(tmp_path / "none.jsonl", 0)


# -- a desktop session -----------------------------------------------------------------------------


async def test_a_desktop_session_idle_for_a_few_minutes_is_copied_to_the_bridge():
    world = World([desktop(updated=NOW - app_sync.DESKTOP_IDLE_SECONDS)])
    await world.keeper().step()
    assert world.copied == [DESK]
    assert world.copies[DESK]["copy"] == "copy-of-09c3f956"
    assert world.copies[DESK]["name"] == "Test session-ab12"
    assert world.recorded[0][0] == "copied_to_bridge"


async def test_a_desktop_session_is_copied_once_it_has_been_idle_a_few_minutes():
    world = World([desktop(updated=NOW - app_sync.DESKTOP_IDLE_SECONDS + 5)])
    await world.keeper().step()
    assert world.copied == []


async def test_a_busy_desktop_session_is_not_copied():
    world = World([desktop(status="busy", updated=NOW - 3600)])
    await world.keeper().step()
    assert world.copied == []


async def test_a_desktop_session_is_copied_to_the_bridge_once():
    world = World([desktop()])
    keeper = world.keeper()
    await keeper.step()
    await keeper.step()
    assert world.copied == [DESK]


async def test_a_desktop_session_copied_before_a_bridge_restart_is_not_copied_again():
    world = World([desktop()])
    world.copies = {DESK: {"copy": "copy-of-09c3f956", "name": "Test session-ab12", "at": NOW - 60}}
    await world.keeper().step()
    assert world.copied == []


async def test_a_desktop_aside_is_not_copied():
    world = World([desktop(name="btw: a side question")])
    await world.keeper().step()
    assert world.copied == []


async def test_a_desktop_window_on_a_conversation_the_bridge_runs_is_not_copied():
    # Opening a bridge session in the desktop app's chat view started a second process on it (Q150).
    world = World([desktop()], records={DESK: ON_RC})
    await world.keeper().step()
    assert world.copied == []


async def test_a_copy_that_fails_is_recorded_once_and_not_tried_again():
    world = World([desktop()])
    world.fail_copy = True
    keeper = world.keeper()
    await keeper.step()
    await keeper.step()
    assert world.copies == {}
    assert [k for k, _ in world.recorded] == ["copy_failed"]


async def test_one_session_failing_does_not_stop_the_others():
    world = World([desktop(), background()], archived=[SID])
    world.fail_copy = True
    await world.keeper().step()
    assert world.stopped == [SID]


# -- resuming a stopped conversation ----------------------------------------------------------------


def test_a_conversation_born_in_the_desktop_app_is_resumed_as_a_copy():
    assert app_sync.how_to_resume(DESK, was_background=False, born_in="desktop", copies={}) == ("copy", DESK)


def test_a_conversation_already_copied_to_the_bridge_resumes_its_copy():
    copies = {DESK: {"copy": "copy-of-09c3f956", "name": "Test session-ab12"}}
    assert app_sync.how_to_resume(DESK, was_background=False, born_in="desktop", copies=copies) == (
        "resume",
        "copy-of-09c3f956",
    )
    assert app_sync.resumed_id(DESK, copies) == "copy-of-09c3f956"


def test_a_conversation_the_bridge_ran_before_is_woken_in_place():
    assert app_sync.how_to_resume(DESK, was_background=True, born_in="desktop", copies={}) == ("resume", DESK)


def test_a_terminal_conversation_is_resumed_in_place():
    assert app_sync.how_to_resume(DESK, was_background=False, born_in="terminal", copies={}) == ("resume", DESK)


# -- the pieces the bridge reads and runs -------------------------------------------------------------


def test_a_job_record_is_read_under_the_sessions_short_id(tmp_path):
    (tmp_path / SID[:8]).mkdir()
    (tmp_path / SID[:8] / "state.json").write_text(json.dumps({"sessionId": SID}), encoding="utf-8")
    assert app_sync.job_record(tmp_path, SID) == {"sessionId": SID}
    assert app_sync.job_record(tmp_path, DESK) is None


def test_a_job_record_of_another_session_under_the_same_short_id_is_not_taken(tmp_path):
    (tmp_path / SID[:8]).mkdir()
    (tmp_path / SID[:8] / "state.json").write_text(json.dumps({"sessionId": SID[:8] + "-other"}), encoding="utf-8")
    assert app_sync.job_record(tmp_path, SID) is None


def test_a_copy_is_started_with_fork_session_after_resume():
    from claude_voice import transcripts

    command = transcripts.background_command("claude", "Test session-ab12", resume=DESK, fork_session=True)
    at = command.index("--resume")
    assert command[at : at + 3] == ["--resume", DESK, "--fork-session"]
    assert "--fork-session" not in transcripts.background_command("claude", "x", resume=DESK)
