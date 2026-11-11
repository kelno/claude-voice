"""The bridge's side of the channels: who is connected, and handing each one its session's messages.

The hub listens on this computer only, on a port it picks, and writes the port and a
secret made at each start to a file in the bridge's state folder. A channel proves it
was started for the bridge by sending that secret first. Which session a channel serves
is found from its process: the Claude Code session that started it is one of its parents.
"""

import asyncio
import json

from claude_voice.channel_hub import ChannelHub, ancestor_session


class Peer:
    """A channel as the hub sees it: a socket that says hello, then reads and acknowledges."""

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    @classmethod
    async def open(cls, link, secret=None, pid=4242):
        info = json.loads(link.read_text(encoding="utf-8"))
        reader, writer = await asyncio.open_connection("127.0.0.1", info["port"])
        peer = cls(reader, writer)
        await peer.send({"secret": secret if secret is not None else info["secret"], "pid": pid})
        return peer

    async def send(self, msg):
        self.writer.write(json.dumps(msg).encode() + b"\n")
        await self.writer.drain()

    async def take(self):
        return json.loads(await asyncio.wait_for(self.reader.readline(), 5))

    async def close(self):
        self.writer.close()


async def connected(hub, session):
    for _ in range(100):
        if hub.connected(session):
            return True
        await asyncio.sleep(0.01)
    return False


async def test_the_hub_listens_on_this_computer_only_and_writes_where_to_find_it(tmp_path):
    link = tmp_path / "state" / "channel.json"
    hub = ChannelHub(link, session_of=lambda pid: None)
    await hub.start()
    try:
        info = json.loads(link.read_text(encoding="utf-8"))
        assert hub.host == "127.0.0.1" and info["port"] == hub.port
        assert len(info["secret"]) >= 32
    finally:
        await hub.stop()


async def test_a_channel_connection_without_the_bridge_s_secret_is_refused(tmp_path):
    link = tmp_path / "channel.json"
    hub = ChannelHub(link, session_of=lambda pid: "sess-1")
    await hub.start()
    try:
        peer = await Peer.open(link, secret="guess")
        assert not await connected(hub, "sess-1")
        assert await hub.deliver("sess-1", "hello") is None
        assert await asyncio.wait_for(peer.reader.read(), 5) == b""  # the hub hung up
    finally:
        await hub.stop()


async def test_a_message_goes_to_the_channel_of_its_session_and_counts_once_acknowledged(tmp_path):
    link = tmp_path / "channel.json"
    hub = ChannelHub(link, session_of={1: "sess-1", 2: "sess-2"}.get)
    await hub.start()
    try:
        one, two = await Peer.open(link, pid=1), await Peer.open(link, pid=2)
        assert await connected(hub, "sess-2")
        sending = asyncio.create_task(hub.deliver("sess-2", "for two"))
        item = await two.take()
        assert item["content"] == "for two"
        await two.send({"ack": item["id"]})
        assert await sending == {"delivered": True, "detail": "", "path": "channel"}
        await one.close()
        await two.close()
    finally:
        await hub.stop()


async def test_a_message_the_channel_took_but_never_confirmed_is_reported_and_not_sent_again(tmp_path):
    link = tmp_path / "channel.json"
    hub = ChannelHub(link, session_of=lambda pid: "sess-1", ack_seconds=0.2)
    await hub.start()
    try:
        peer = await Peer.open(link)
        assert await connected(hub, "sess-1")
        out = await hub.deliver("sess-1", "hello")
        assert out["delivered"] is False and out["path"] == "channel"
        assert "did not confirm" in out["detail"]
        assert (await peer.take())["content"] == "hello"
    finally:
        await hub.stop()


async def test_a_session_with_no_channel_is_left_to_the_courier(tmp_path):
    hub = ChannelHub(tmp_path / "channel.json", session_of=lambda pid: None)
    await hub.start()
    try:
        assert await hub.deliver("sess-9", "hello") is None
        assert hub.status() == {"listening": True, "connected": 0}
    finally:
        await hub.stop()


async def test_a_channel_that_hangs_up_is_no_longer_connected(tmp_path):
    link = tmp_path / "channel.json"
    hub = ChannelHub(link, session_of=lambda pid: "sess-1")
    await hub.start()
    try:
        peer = await Peer.open(link)
        assert await connected(hub, "sess-1")
        await peer.close()
        for _ in range(100):
            if not hub.connected("sess-1"):
                break
            await asyncio.sleep(0.01)
        assert not hub.connected("sess-1")
    finally:
        await hub.stop()


def test_a_channels_session_is_the_claude_code_session_among_its_parents():
    # The channel may run under a launcher (uv, a shell), so its direct parent need not be
    # Claude Code: the first parent with a session record names the session.
    parents = {300: [200, 100, 1]}
    live = [{"pid": 100, "sessionId": "sess-1"}, {"pid": 999, "sessionId": "other"}]
    assert ancestor_session(300, lambda: live, parents=parents.get) == "sess-1"
    assert ancestor_session(300, lambda: [], parents=parents.get) is None
    assert ancestor_session(5, lambda: live, parents=lambda pid: None) is None
