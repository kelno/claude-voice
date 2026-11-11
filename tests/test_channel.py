"""The channel: a small MCP server each bridge-started session runs, carrying the relay's messages in.

Claude Code starts it as a subprocess and talks MCP over its stdin and stdout. It declares
the ``claude/channel`` capability and turns each message the bridge hands it into a
``notifications/claude/channel`` event, which starts a turn in an idle session. No model
sits in between, unlike the courier.
"""

import asyncio
import json
import threading

from claude_voice import channel
from claude_voice.channel_hub import ChannelHub


def request(method, mid=1, **params):
    return {"jsonrpc": "2.0", "id": mid, "method": method, "params": params}


def test_the_channel_declares_itself_a_channel_and_speaks_the_clients_protocol():
    out = channel.answer(request("initialize", protocolVersion="2025-11-25"))
    result = out["result"]
    assert out["id"] == 1
    assert result["protocolVersion"] == "2025-11-25"
    assert result["capabilities"]["experimental"]["claude/channel"] == {}
    assert result["serverInfo"]["name"] == "claude-voice"


def test_the_channel_tells_the_session_a_relayed_message_is_not_the_owners_word():
    text = channel.answer(request("initialize", protocolVersion="2025-11-25"))["result"]["instructions"]
    assert '<channel source="claude-voice"' in text
    assert "not the owner's" in text and "confirm_ask" in text
    assert len(text) <= 2048  # Claude Code cuts a server's instructions past that


def test_the_channel_refuses_the_newer_protocols_discovery_so_the_client_falls_back():
    # Claude Code registers no channel that negotiates MCP's 2026-07-28 revision, so its
    # discovery probe gets "no such method" and the client falls back to initialize.
    out = channel.answer(request("server/discover", mid="probe"))
    assert out["id"] == "probe" and out["error"]["code"] == -32601


def test_the_channel_answers_pings_and_offers_no_tools():
    assert channel.answer(request("ping"))["result"] == {}
    assert channel.answer(request("tools/list"))["result"] == {"tools": []}
    assert channel.answer({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_a_relayed_message_becomes_one_channel_event_with_its_text_unchanged():
    out = channel.event({"id": 7, "content": "Hello\n\nsecond line"})
    assert out == {
        "jsonrpc": "2.0",
        "method": "notifications/claude/channel",
        "params": {"content": "Hello\n\nsecond line", "meta": {"via": "claude-voice bridge"}},
    }


def test_each_message_is_acknowledged_only_after_its_event_is_written():
    order = []
    lines = [json.dumps({"id": 1, "content": "one"}).encode() + b"\n", b"\n", b"not json\n"]
    channel.carry(
        lines, write=lambda m: order.append(("event", m["params"]["content"])), ack=lambda i: order.append(("ack", i))
    )
    assert order == [("event", "one"), ("ack", 1)]


async def test_a_message_reaches_the_session_through_the_bridges_hub(tmp_path):
    link = tmp_path / "channel.json"
    hub = ChannelHub(link, session_of=lambda pid: "sess-1" if pid == 4242 else None)
    await hub.start()
    events, stop = [], threading.Event()
    ready = threading.Event()
    ready.set()
    worker = threading.Thread(
        target=channel.bridge_loop, args=(events.append, ready, link, 4242), kwargs={"stop": stop, "pause": 0.05}
    )
    worker.start()
    try:
        for _ in range(100):
            if hub.connected("sess-1"):
                break
            await asyncio.sleep(0.02)
        out = await hub.deliver("sess-1", "Hi from the relay")
    finally:
        stop.set()
        await hub.stop()
        await asyncio.to_thread(worker.join, 5)
    assert out["delivered"] is True and out["path"] == "channel"
    assert [e["params"]["content"] for e in events] == ["Hi from the relay"]
