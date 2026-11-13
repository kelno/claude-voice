"""A session that started without its claude.ai connectors is noticed and restarted.

Seen 2026-11-13: the PC woke, Claude Code's daemon respawned a background session two
seconds later, and that session came back with none of its claude.ai connectors while the
bridge's channel plugin still reached it. A stop and a resume brought them all back. Claude
Code writes a log file per MCP server at each start, named by the start's time: the respawn
had one for the channel plugin and none for a claude.ai connector.
"""

import json
from datetime import UTC, datetime

from claude_voice import connectors
from claude_voice.transcripts import project_key_for_directory

SID = "252eb71d-d82d-4a4b-9212-8ba291e1e692"
OTHER = "11111111-2222-3333-4444-555555555555"
CWD = "D:\\sync\\SyncAll\\Obsidian"
WAKE = "2026-11-13T13-02-04-843Z"  # the respawn after the wake: the channel plugin only
EARLIER = "2026-11-13T00-21-29-715Z"  # a start the night before, with every connector


def at(stem):
    return datetime.strptime(stem, "%Y-%m-%dT%H-%M-%S-%fZ").replace(tzinfo=UTC).timestamp()


def log(cache, server, stem, sid=SID, cwd=CWD):
    folder = cache / project_key_for_directory(cwd) / f"mcp-logs-{server}"
    folder.mkdir(parents=True, exist_ok=True)
    line = {"debug": "Starting connection", "timestamp": "x", "sessionId": sid, "cwd": cwd}
    (folder / f"{stem}.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")


def full_start(cache, stem, sid=SID, cwd=CWD):
    for server in ("plugin-relay-channel-claude-voice", "claude-ai-Obsidian-MCP", "claude-ai-Claude-Voice"):
        log(cache, server, stem, sid, cwd)


def row(stem, status="idle", sid=SID, kind="bg"):
    started = int(at(stem) * 1000) - 600  # the process starts just before it logs its servers
    return {"sessionId": sid, "cwd": CWD, "startedAt": started, "status": status, "kind": kind, "name": "Desk"}


def test_a_start_with_the_plugin_and_no_connector_is_missing_them(tmp_path):
    full_start(tmp_path, EARLIER)
    log(tmp_path, "plugin-relay-channel-claude-voice", WAKE)
    assert connectors.missing(tmp_path, row(WAKE), now=at(WAKE) + 120) is True


def test_a_start_with_its_connectors_is_not_missing_them(tmp_path):
    full_start(tmp_path, WAKE)
    assert connectors.missing(tmp_path, row(WAKE), now=at(WAKE) + 120) is False


def test_a_start_is_not_judged_before_its_connectors_had_time_to_load(tmp_path):
    full_start(tmp_path, EARLIER)
    log(tmp_path, "plugin-relay-channel-claude-voice", WAKE)
    assert connectors.missing(tmp_path, row(WAKE), now=at(WAKE) + 30) is None


def test_a_folder_where_no_connector_ever_loaded_tells_nothing(tmp_path):
    # An account without claude.ai connectors, or a folder they never loaded in.
    log(tmp_path, "plugin-relay-channel-claude-voice", WAKE)
    assert connectors.missing(tmp_path, row(WAKE), now=at(WAKE) + 120) is None


def test_another_sessions_start_in_the_same_folder_is_not_taken_for_this_one(tmp_path):
    full_start(tmp_path, EARLIER)
    log(tmp_path, "plugin-relay-channel-claude-voice", WAKE, sid=OTHER)
    assert connectors.missing(tmp_path, row(WAKE), now=at(WAKE) + 120) is None


def test_an_earlier_start_of_the_same_session_does_not_count(tmp_path):
    # The night before it had them; what counts is the process running now.
    full_start(tmp_path, EARLIER)
    assert connectors.missing(tmp_path, row(WAKE), now=at(WAKE) + 120) is None


def test_no_log_folder_at_all_tells_nothing(tmp_path):
    assert connectors.missing(tmp_path / "nowhere", row(WAKE), now=at(WAKE) + 120) is None


def test_a_session_that_moved_into_a_worktree_is_judged_by_the_folder_it_started_in(tmp_path):
    # Seen 2026-11-13: a session started in the repository, then entered a worktree under it;
    # Claude Code kept its logs under the repository's folder.
    full_start(tmp_path, EARLIER)
    log(tmp_path, "plugin-relay-channel-claude-voice", WAKE)
    moved = dict(row(WAKE), cwd=CWD + "\\.claude\\worktrees\\compact")
    assert connectors.missing(tmp_path, moved, now=at(WAKE) + 120) is True


def test_a_start_later_than_now_is_not_this_ones(tmp_path):
    # Read back in time, as when checking an old case: a later start isn't there yet.
    full_start(tmp_path, EARLIER)
    log(tmp_path, "plugin-relay-channel-claude-voice", WAKE)
    full_start(tmp_path, "2026-11-13T17-30-39-014Z")
    assert connectors.missing(tmp_path, row(WAKE), now=at(WAKE) + 120) is True


class World:
    """The bridge as the mender sees it: running sessions, and what it can do to them."""

    def __init__(self, rows, missing, started_here=True, reachable=True):
        self.rows = rows
        self.missing_for = missing  # sessionId -> what missing() says
        self.started_here = started_here
        self.up = reachable
        self.now = 1000.0
        self.restarted, self.sent, self.recorded = [], [], []

    def mender(self):
        async def restart(d):
            self.restarted.append(d["sessionId"])
            new = dict(d, startedAt=d["startedAt"] + 60_000)
            self.rows = [new if r["sessionId"] == d["sessionId"] else r for r in self.rows]

        async def send(d, text):
            self.sent.append((d["sessionId"], text))
            return {"delivered": True}

        async def reachable():
            return self.up

        return connectors.Mender(
            live=lambda: self.rows,
            missing=lambda d: self.missing_for(d),
            started_here=lambda d: self.started_here,
            at_rest=lambda d: d["status"] == "idle",
            reachable=reachable,
            restart=restart,
            send=send,
            record=lambda name, kind, text: self.recorded.append((kind, text)),
            clock=lambda: self.now,
        )


async def test_a_session_at_rest_without_its_connectors_is_restarted_once():
    world = World([row(WAKE)], missing=lambda d: True)
    mender = world.mender()
    await mender.step()
    assert world.restarted == [SID] and world.sent == []
    assert world.recorded and world.recorded[0][0] == "connectors_restarted"


async def test_a_session_with_its_connectors_is_left_alone():
    world = World([row(WAKE)], missing=lambda d: False)
    await world.mender().step()
    assert world.restarted == [] and world.sent == []


async def test_a_session_the_logs_cant_judge_is_left_alone():
    world = World([row(WAKE)], missing=lambda d: None)
    await world.mender().step()
    assert world.restarted == [] and world.sent == []


async def test_a_busy_session_is_not_restarted_mid_turn():
    world = World([row(WAKE, status="busy")], missing=lambda d: True)
    await world.mender().step()
    assert world.restarted == [] and world.sent == []


async def test_a_session_busy_for_ten_minutes_is_told_it_will_be_restarted_at_rest():
    world = World([row(WAKE, status="busy")], missing=lambda d: True)
    mender = world.mender()
    await mender.step()
    world.now += connectors.BUSY_WARNING_SECONDS
    await mender.step()
    await mender.step()  # told once
    assert world.sent == [(SID, connectors.BUSY_MESSAGE)]
    world.rows = [row(WAKE)]  # its turn ends: the bridge still restarts it
    await mender.step()
    assert world.restarted == [SID]


async def test_a_session_is_not_restarted_while_claude_ai_cannot_be_reached():
    world = World([row(WAKE)], missing=lambda d: True, reachable=False)
    mender = world.mender()
    await mender.step()
    assert world.restarted == []
    world.up = True
    await mender.step()
    assert world.restarted == [SID]


async def test_a_session_the_bridge_did_not_start_is_told_to_warn_the_owner():
    world = World([row(WAKE)], missing=lambda d: True, started_here=False)
    mender = world.mender()
    await mender.step()
    await mender.step()
    assert world.restarted == []
    assert world.sent == [(SID, connectors.NOT_STARTED_MESSAGE)]


async def test_a_restart_that_comes_back_without_them_is_not_tried_again_and_the_session_is_told():
    world = World([row(WAKE)], missing=lambda d: True)
    mender = world.mender()
    await mender.step()  # restarted
    await mender.step()  # the new start lacks them too
    await mender.step()
    assert world.restarted == [SID]
    assert world.sent == [(SID, connectors.RESTART_FAILED_MESSAGE)]
    assert [kind for kind, _ in world.recorded] == ["connectors_restarted", "connectors_still_missing"]


async def test_a_restart_that_raises_counts_as_failed():
    world = World([row(WAKE)], missing=lambda d: True)
    mender = world.mender()

    async def broken(d):
        raise RuntimeError("Claude Code could not stop it")

    mender.restart = broken
    await mender.step()
    await mender.step()
    assert world.sent == [(SID, connectors.RESTART_FAILED_MESSAGE)]


async def test_a_session_whose_connectors_came_back_can_be_mended_again_on_a_later_start():
    world = World([row(WAKE)], missing=lambda d: True)
    mender = world.mender()
    await mender.step()  # restarted
    world.missing_for = lambda d: False
    await mender.step()  # the restart worked
    world.rows = [dict(world.rows[0], startedAt=world.rows[0]["startedAt"] + 3_600_000)]  # another wake
    world.missing_for = lambda d: True
    await mender.step()
    assert world.restarted == [SID, SID] and world.sent == []


async def test_one_session_failing_does_not_stop_the_others():
    rows = [row(WAKE, sid=OTHER), row(WAKE)]

    def missing(d):
        if d["sessionId"] == OTHER:
            raise OSError("unreadable log")
        return True

    world = World(rows, missing=missing)
    await world.mender().step()
    assert world.restarted == [SID]


def test_the_messages_say_who_sends_them_and_what_to_tell_the_owner():
    for text in (connectors.BUSY_MESSAGE, connectors.NOT_STARTED_MESSAGE, connectors.RESTART_FAILED_MESSAGE):
        assert text.startswith("From the claude-voice bridge, not the owner:")
        assert "next reply to the owner" in text
    assert "as soon as" in connectors.BUSY_MESSAGE
    assert "can't restart" in connectors.NOT_STARTED_MESSAGE
    assert "didn't bring them back" in connectors.RESTART_FAILED_MESSAGE
