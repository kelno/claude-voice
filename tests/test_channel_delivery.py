"""Which way a relayed message goes: the session's channel when it has one, else the courier.

The channel hands a message to Claude Code at once, with no model in between; the courier
is a small model session that sends it with SendMessage, and still carries every message
to a session with no channel (desktop sessions, sessions started by hand).
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


async def test_a_session_with_no_channel_is_reached_by_the_courier(world):
    hub, courier = Hub(None), Courier()
    async with Client(bridge(world, hub, courier)) as c:
        out = (
            await c.call_tool("message_active_session", {"session": "billing", "message": "hello"})
        ).structured_content
    assert out["delivered"] is True
    assert [name for name, _ in courier.carried] == ["billing"]


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
