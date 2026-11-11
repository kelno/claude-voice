"""The bridge's side of the channels: who is connected, and handing each one its session's messages.

Each session the bridge starts runs a channel (``channel.py``), which connects here. The
hub listens on this computer only, on a port it picks, and writes the port and a secret
made at each start to a file in the bridge's state folder; a channel proves it was
started for this bridge by sending that secret first. Which session a channel serves is
found when a message is sent, from the channel's process: the Claude Code session that
started it is one of its parents, and has a record in Claude Code's session registry.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psutil

log = logging.getLogger(__name__)

HOST = "127.0.0.1"
ACK_SECONDS = 5.0  # how long a channel has to confirm it passed a message on


def _parents(pid: int) -> list[int] | None:
    try:
        return [p.pid for p in psutil.Process(pid).parents()]
    except psutil.Error:
        return None


def ancestor_session(
    pid: int,
    live: Callable[[], list[dict[str, Any]]],
    parents: Callable[[int], list[int] | None] = _parents,
) -> str | None:
    """The session whose Claude Code process is the nearest parent of process `pid`, if any."""
    chain = parents(pid)
    if not chain:
        return None
    by_pid = {int(d["pid"]): str(d["sessionId"]) for d in live() if "pid" in d and "sessionId" in d}
    return next((by_pid[p] for p in chain if p in by_pid), None)


class _Link:
    """One connected channel: its process, how to write to it, and the messages awaiting its word."""

    def __init__(self, pid: int, writer: asyncio.StreamWriter) -> None:
        self.pid = pid
        self.writer = writer
        self.waiting: dict[int, asyncio.Future[None]] = {}


class ChannelHub:
    def __init__(
        self,
        link: Path,
        session_of: Callable[[int], str | None],
        ack_seconds: float = ACK_SECONDS,
    ) -> None:
        self.link = link.expanduser()
        self.session_of = session_of
        self.ack_seconds = ack_seconds
        self.host = HOST
        self.port = 0
        self._secret = ""
        self._server: asyncio.Server | None = None
        self._links: list[_Link] = []
        self._next = 0

    async def start(self) -> None:
        self._secret = secrets.token_urlsafe(32)
        self._server = await asyncio.start_server(self._serve, self.host, 0)
        self.port = int(self._server.sockets[0].getsockname()[1])
        self.link.parent.mkdir(parents=True, exist_ok=True)
        self.link.write_text(json.dumps({"port": self.port, "secret": self._secret}), encoding="utf-8")

    async def stop(self) -> None:
        for link in list(self._links):
            link.writer.close()
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            hello = json.loads(await asyncio.wait_for(reader.readline(), 10))
            given = str(hello.get("secret", ""))
            # RULE: a channel connection without the bridge's secret is refused
            if not secrets.compare_digest(given.encode(), self._secret.encode()):
                writer.close()
                return
            link = _Link(int(hello["pid"]), writer)
        except (TimeoutError, ValueError, KeyError, TypeError, AttributeError, OSError):
            writer.close()
            return
        self._links.append(link)
        try:
            while line := await reader.readline():
                with contextlib.suppress(ValueError, TypeError, AttributeError):
                    done = link.waiting.get(int(json.loads(line)["ack"]))
                    if done is not None and not done.done():
                        done.set_result(None)
        except (OSError, KeyError):
            pass
        finally:
            self._links.remove(link)
            writer.close()

    def _link_of(self, session: str) -> _Link | None:
        return next((link for link in reversed(self._links) if self.session_of(link.pid) == session), None)

    def connected(self, session: str) -> bool:
        return self._link_of(session) is not None

    def status(self) -> dict[str, Any]:
        return {"listening": self._server is not None, "connected": len(self._links)}

    async def deliver(self, session: str, text: str) -> dict[str, Any] | None:
        """Hand `text` to the channel of `session`; None when that session has no channel.

        A message written to a channel that then never confirms it is reported as not
        delivered, and not sent again any other way: it may already be in the session.
        """
        link = self._link_of(session)
        if link is None:
            return None
        self._next += 1
        mid = self._next
        done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        link.waiting[mid] = done
        try:
            link.writer.write(json.dumps({"id": mid, "content": text}).encode() + b"\n")
            await link.writer.drain()
            await asyncio.wait_for(done, self.ack_seconds)
        except (TimeoutError, OSError):
            return {
                "delivered": False,
                "detail": "the session's channel did not confirm the message",
                "path": "channel",
            }
        finally:
            link.waiting.pop(mid, None)
        return {"delivered": True, "detail": "", "path": "channel"}
