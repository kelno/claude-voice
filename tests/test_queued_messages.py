"""A relayed message to a busy session, read through the raw transcript reader as in production.

Claude Code queues a message to a busy session until its turn ends: of 336 relay-channel lines in
this PC's transcripts (2026-11-14), none landed mid-turn. So the end of the turn the session was
on comes between the send and the message's own line. Scenarios and helpers from the reviewer of
the branch that made relay-channel messages count as turns.
"""

import asyncio
import json

import pytest
from test_news import Desk, bridge, call, conversation, world  # noqa: F401  (world is a fixture)

from claude_voice import transcripts
from claude_voice.store import Store
from claude_voice.transcripts import project_key_for_directory


class Hub:
    """The bridge's channel hub: it takes each message and confirms it, adding nothing itself."""

    def __init__(self):
        self.handed = []
        self.took = asyncio.Event()

    async def start(self):
        pass

    async def stop(self):
        pass

    async def deliver(self, session, text):
        self.handed.append(text)
        self.took.set()
        return {"delivered": True, "detail": "", "path": "channel"}

    def status(self):
        return {"listening": True, "connected": 1}


class RawLog:
    """The session's transcript file, read by the bridge's own raw reader."""

    def __init__(self, projects, cwd, sid):
        self.projects, self.cwd, self.sid = projects, cwd, sid
        self.path = projects / project_key_for_directory(cwd) / f"{sid}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("")

    def add(self, entry):
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def __iter__(self):
        return iter(transcripts.read_raw_transcript(self.sid, self.cwd, self.projects))


def typed(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def said(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def channel(text):
    body = f'<channel source="plugin:relay-channel:claude-voice" via="claude-voice bridge">\n{text}\n</channel>'
    return {
        "type": "user",
        "isMeta": True,
        "origin": {"kind": "channel", "server": "plugin:relay-channel:claude-voice"},
        "message": {"role": "user", "content": body},
    }


def courier(text):
    tag = '<cross-session-message from="uds:x" from-name="Relay" from-mode="prompting">'
    return {
        "type": "user",
        "isMeta": True,
        "origin": {"kind": "peer", "name": "Relay"},
        "message": {"role": "user", "content": f"Another Claude session sent a message:\n{tag}\n{text}\n"},
    }


def busy_on_the_owners_turn(tmp_path, world):  # noqa: F811
    root, live = world
    desk = Desk(live, str(root / "billing"))
    log = RawLog(tmp_path / "projects", desk.cwd, desk.sid)
    log.add(typed("earlier question"))
    log.add(said("earlier answer"))
    log.add(typed("Fix the failing tests."))  # the owner typed this at the PC
    desk.transcript = log
    desk.set("busy")
    return desk, log


@pytest.mark.parametrize("landed", [channel, courier])
async def test_a_follow_waits_for_its_own_message_to_land(tmp_path, world, landed):  # noqa: F811
    desk, log = busy_on_the_owners_turn(tmp_path, world)
    hub = Hub()
    async with bridge(tmp_path, desk, Store(tmp_path / "bridge.db"), channels=hub) as url:
        http, client = conversation(url)
        async with http, client as c:
            await call(c, "message_active_session", session="billing-ab", message="Which folder?")
            log.add(said("Tests fixed."))  # the end of the owner's turn
            log.add(landed(hub.handed[0]))  # the relay's message, queued until then
            busy = await call(c, "list_active_sessions")  # the session now works on the relay's message
            log.add(said("In billing."))
            desk.set("idle")
            answered = await call(c, "list_active_sessions")
            read = await call(c, "wait_for_session")
    assert busy["news"] == "[Juniper] billing-ab (busy): nothing new"
    assert answered["news"].startswith("[Juniper] billing-ab (idle): reply to you")
    assert read["reply"] == "In billing." and read["reply_final"] is True


async def test_a_message_queued_to_the_end_of_a_turn_is_not_answered_mid_work(tmp_path, world):  # noqa: F811
    desk, log = busy_on_the_owners_turn(tmp_path, world)
    hub = Hub()

    async def session_works():
        await hub.took.wait()
        await asyncio.sleep(0.02)
        log.add(said("Tests fixed."))  # the owner's turn ends
        log.add(channel(hub.handed[0]))  # the question, queued until then
        await asyncio.sleep(0.05)
        log.add(said("Let me look at the folders."))  # a progress line
        await asyncio.sleep(0.1)
        log.add(said("In billing."))  # the answer
        desk.set("idle")

    async with bridge(tmp_path, desk, Store(tmp_path / "bridge.db"), channels=hub) as url:
        http, client = conversation(url)
        async with http, client as c:
            work = asyncio.create_task(session_works())
            out = await call(c, "ask_active_session", session="billing-ab", message="Which folder?", wait_seconds=5)
            await work
    assert (out["status"], out["reply"], out["reply_final"]) == ("answered", "In billing.", True)


async def test_a_message_the_owner_typed_is_never_taken_for_the_relay_s_own(tmp_path, world):  # noqa: F811
    # A short relayed message ("Yes, go ahead") can match words the owner typed in the session
    # while the relay's message waited for the turn to end; the follow must not start from them.
    desk, log = busy_on_the_owners_turn(tmp_path, world)
    hub = Hub()
    async with bridge(tmp_path, desk, Store(tmp_path / "bridge.db"), channels=hub) as url:
        http, client = conversation(url)
        async with http, client as c:
            await call(c, "message_active_session", session="billing-ab", message="Yes, go ahead")
            log.add(typed("Yes, go ahead and push too."))  # the owner, at the PC
            log.add(said("Tests fixed."))  # the end of the owner's turn
            log.add(channel(hub.handed[0]))  # the relay's message, queued until then
            busy = await call(c, "list_active_sessions")
    assert busy["news"] == "[Juniper] billing-ab (busy): nothing new"
