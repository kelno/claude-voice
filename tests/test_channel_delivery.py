"""How a relayed message reaches a session: through the session's channel, and no other way.

The channel hands a message to Claude Code at once, with no model in between. The courier, a
small model session that sent it with SendMessage, carried every message to a session with no
channel (desktop sessions, sessions started by hand); the served bridge no longer uses it, since
every session is to run under the bridge (owner, 2026-11-21). Only a bridge built without
channels, upstream's way, still sends by courier.
"""

import json
import os

import pytest
from fakes import FakeClaude
from mcp.client import Client

from claude_voice import transcripts
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

PID = os.getpid()


class Hub:
    """Stands in for the channel hub: connected sessions, and what each was handed."""

    def __init__(self, answer):
        self.answer = answer  # what deliver returns: a result, or None for "no channel"
        self.handed = []
        self.started = self.stopped = False

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def deliver(self, session, text):
        self.handed.append((session, text))
        return self.answer

    def status(self):
        return {"listening": True, "connected": 1}


class Courier:
    def __init__(self):
        self.carried = []

    async def __call__(self, name, text):
        self.carried.append((name, text))
        return {"delivered": True, "detail": ""}


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "src"
    (root / "billing").mkdir(parents=True)
    live = tmp_path / "live"
    live.mkdir()
    (live / f"{PID}.json").write_text(
        json.dumps(
            {"pid": PID, "sessionId": "sid-1", "cwd": str(root / "billing"), "name": "billing", "status": "idle"}
        )
    )
    return tmp_path, root, live


def bridge(world, hub, courier):
    tmp_path, root, live = world
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    return build_server(
        m,
        conversations=lambda directory=None, limit=None: [],
        live_dir=live,
        deliver=courier,
        channels=hub,
        read_transcript=lambda sid, directory: [],
        poll_seconds=0.01,
        projects_dir=tmp_path / "projects",
    )


async def test_a_session_with_a_connected_channel_gets_the_message_through_it_not_the_courier(world):
    hub, courier = Hub({"delivered": True, "detail": "", "path": "channel"}), Courier()
    async with Client(bridge(world, hub, courier)) as c:
        out = (
            await c.call_tool("message_active_session", {"session": "billing", "message": "hello"})
        ).structured_content
    assert out["delivered"] is True
    assert [s for s, _ in hub.handed] == ["sid-1"]
    assert hub.handed[0][1].startswith("hello\n\n[From the claude-voice bridge")  # the relay note still goes along
    assert courier.carried == []


async def test_a_session_without_a_channel_is_not_reached_by_the_courier(world):
    # Seen 2026-11-21: of a week's messages, the courier carried 46, all to sessions the owner had
    # opened in the desktop app or by hand; the owner dropped those from what the bridge serves.
    hub, courier = Hub(None), Courier()
    async with Client(bridge(world, hub, courier)) as c:
        result = await c.call_tool("message_active_session", {"session": "billing", "message": "hello"})
        asked = await c.call_tool("ask_active_session", {"session": "billing", "message": "hi?", "wait_seconds": 0.05})
    assert result.is_error and asked.is_error and courier.carried == []
    said = result.content[0].text
    assert "no relay channel" in said and "desktop app" in said
    assert "sign-in" not in said.lower()  # nothing to do with Claude Code's sign-in


async def test_a_session_started_with_the_channel_is_waited_for_until_it_connects(world, monkeypatch):
    # Right after a start or a resume, the session runs before its channel has connected; the
    # courier used to carry such a message.
    from claude_voice import channel_hub, server

    class Late(Hub):
        async def deliver(self, session, text):
            self.handed.append((session, text))
            return self.answer if len(self.handed) > 3 else None

    monkeypatch.setattr(channel_hub, "started_with_channel", lambda pid: pid == PID)
    monkeypatch.setattr(server, "CHANNEL_CONNECT_POLL_SECONDS", 0.01)
    hub, courier = Late({"delivered": True, "detail": "", "path": "channel"}), Courier()
    async with Client(bridge(world, hub, courier)) as c:
        out = (
            await c.call_tool("message_active_session", {"session": "billing", "message": "hello"})
        ).structured_content
    assert out["delivered"] is True and len(hub.handed) == 4 and courier.carried == []


async def test_a_bridge_without_channels_still_sends_by_courier(world):
    tmp_path, root, live = world
    courier = Courier()
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    srv = build_server(
        m,
        conversations=lambda directory=None, limit=None: [],
        live_dir=live,
        deliver=courier,
        read_transcript=lambda sid, directory: [],
        poll_seconds=0.01,
        projects_dir=tmp_path / "projects",
    )
    async with Client(srv) as c:
        out = (
            await c.call_tool("message_active_session", {"session": "billing", "message": "hello"})
        ).structured_content
    assert out["delivered"] is True and [name for name, _ in courier.carried] == ["billing"]


async def test_a_message_the_channel_took_but_never_confirmed_is_not_sent_again_by_the_courier(world):
    hub = Hub({"delivered": False, "detail": "the session's channel did not confirm the message", "path": "channel"})
    courier = Courier()
    async with Client(bridge(world, hub, courier)) as c:
        result = await c.call_tool("message_active_session", {"session": "billing", "message": "hello"})
    assert result.is_error and "did not confirm" in result.content[0].text
    assert courier.carried == []


async def test_asking_a_session_goes_through_its_channel_too(world):
    hub, courier = Hub({"delivered": True, "detail": "", "path": "channel"}), Courier()
    async with Client(bridge(world, hub, courier)) as c:
        await c.call_tool("ask_active_session", {"session": "billing", "message": "what now?", "wait_seconds": 0.05})
    assert [s for s, _ in hub.handed] == ["sid-1"] and courier.carried == []


async def test_health_says_how_many_channels_are_connected(world):
    hub = Hub(None)
    async with Client(bridge(world, hub, Courier())) as c:
        out = (await c.call_tool("health", {})).structured_content
    assert out["channels"] == {"listening": True, "connected": 1}


def test_a_session_the_bridge_starts_runs_the_relay_channel_when_it_is_set_up():
    command = transcripts.background_command("claude", "job", prompt="go", channel=True)
    settings = json.loads(command[command.index("--settings") + 1])
    assert settings["enabledPlugins"] == {"relay-channel@claude-voice": True}
    assert command[command.index("--channels") + 1] == "plugin:relay-channel@claude-voice"
    # --channels takes a list, so it must not be the last flag before the first message.
    assert command.index("--channels") < command.index("--settings") < command.index("--")


def test_without_the_channel_set_up_a_session_starts_as_before():
    command = transcripts.background_command("claude", "job", prompt="go")
    assert "--channels" not in command
    assert "enabledPlugins" not in json.loads(command[command.index("--settings") + 1])


def test_the_channel_counts_as_set_up_once_its_plugin_is_installed(tmp_path):
    record = tmp_path / "installed_plugins.json"
    assert transcripts.channel_installed(record) is False
    record.write_text(json.dumps({"version": 2, "plugins": {"relay-channel@claude-voice": [{"scope": "user"}]}}))
    assert transcripts.channel_installed(record) is True
    record.write_text(json.dumps({"version": 2, "plugins": {}}))
    assert transcripts.channel_installed(record) is False
