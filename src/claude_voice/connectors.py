"""Sessions that started without their claude.ai connectors, noticed and restarted.

Seen 2026-11-13: the PC woke from sleep, Claude Code's daemon respawned a background
session two seconds later, before the network was back, and that session ran without any
of its claude.ai connectors (Claude Voice, the vault, the others) while the bridge's
channel plugin still reached it. Claude Code doesn't fetch them again in a running session;
a stop and a resume brought them all back.

Claude Code writes one log file per MCP server each time a session's process starts, in
its cache folder for the session's project, named by the start's time and naming the
session on each line. A start with a log for some server but none for a `claude-ai-*` one,
in a folder where they have loaded before, came without its connectors. These are Claude
Code's own files, not a documented interface: anything unexpected in them tells nothing,
and nothing is done.
"""

import contextlib
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .transcripts import project_key_for_directory

log = logging.getLogger(__name__)

CACHE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path("~/AppData/Local").expanduser()) / "claude-cli-nodejs" / "Cache"
CONNECTOR = "mcp-logs-claude-ai-"  # the log folders of claude.ai connectors
LOAD_SECONDS = 90  # how long a start's connectors may take to show up in its logs
EARLY_SECONDS = 5  # a process logs its servers a moment after it starts
BUSY_WARNING_SECONDS = 600  # a session busy this long without its connectors is told
PROXY = "https://mcp-proxy.anthropic.com/"  # where Claude Code reaches claude.ai connectors

BUSY_MESSAGE = (
    "From the claude-voice bridge, not the owner: this session started without its claude.ai connectors "
    "(Claude Voice, Obsidian-MCP and the others are missing from your tools). The bridge will restart it as "
    "soon as your turn ends and nothing of yours is still running; the conversation is kept and the connectors "
    "should come back. Until then, at the start of your next reply to the owner, say in one line that your "
    "claude.ai connectors are missing and will return when this session is restarted at rest. Then carry on "
    "with what you can do without them."
)
NOT_STARTED_MESSAGE = (
    "From the claude-voice bridge, not the owner: this session started without its claude.ai connectors "
    "(Claude Voice, Obsidian-MCP and the others are missing from your tools), and the bridge can't restart it, "
    "since it didn't start this session. At the start of your next reply to the owner, say in one line that "
    "your claude.ai connectors are missing and that stopping and resuming this session should bring them "
    "back. Then carry on with what you can do without them."
)
RESTART_FAILED_MESSAGE = (
    "From the claude-voice bridge, not the owner: this session started without its claude.ai connectors "
    "(Claude Voice, Obsidian-MCP and the others are missing from your tools), and the bridge's restart didn't "
    "bring them back. At the start of your next reply to the owner, say in one line that your claude.ai "
    "connectors are missing and that the bridge's restart didn't fix it. Then carry on with what you can do "
    "without them."
)


def _start_time(stem: str) -> float | None:
    """When a log file's start was, from its name (2026-11-13T13-02-04-843Z), or None."""
    try:
        return datetime.strptime(stem, "%Y-%m-%dT%H-%M-%S-%fZ").replace(tzinfo=UTC).timestamp()
    except ValueError:
        return None


def _session_of(path: Path) -> str | None:
    """The session a log file belongs to, from its first line, or None."""
    try:
        with path.open(encoding="utf-8") as f:
            return json.loads(f.readline()).get("sessionId")
    except (OSError, ValueError, AttributeError):
        return None


def missing(cache: Path, d: dict[str, Any], now: float | None = None) -> bool | None:
    """Whether the running session `d` started without its claude.ai connectors: True or
    False, or None when its logs can't tell yet (or at all).

    Claude Code keeps the logs under the folder the process started in: the session's
    folder or, for one that moved into a worktree since, a folder above it."""
    now = time.time() if now is None else now
    try:
        started = float(d["startedAt"]) / 1000 - EARLY_SECONDS
        here = Path(str(d["cwd"]))
    except (KeyError, TypeError, ValueError):
        return None
    for folder in (here, *here.parents):
        verdict = _judged(cache / project_key_for_directory(str(folder)), str(d.get("sessionId")), started, now)
        if verdict is not None:
            return verdict if isinstance(verdict, bool) else None  # "not yet" tells nothing yet
    return None


def _judged(folder: Path, sid: str, started: float, now: float) -> bool | str | None:
    """What one log folder says of the session's latest start: True or False, "not yet"
    while its connectors may still be loading, or None when it holds none of its starts."""
    try:
        servers = [p for p in folder.iterdir() if p.is_dir() and p.name.startswith("mcp-logs-")]
    except OSError:
        return None
    # RULE: a folder where no connector ever loaded tells nothing
    if not any(p.name.startswith(CONNECTOR) for p in servers):
        return None
    starts: dict[str, list[Path]] = {}  # each start since this process began: its log files
    for server in servers:
        with contextlib.suppress(OSError):
            for f in server.glob("*.jsonl"):
                at = _start_time(f.stem)
                if at is not None and started <= at <= now:
                    starts.setdefault(f.stem, []).append(f)
    mine = [stem for stem, files in starts.items() if any(_session_of(f) == sid for f in files)]
    if not mine:
        return None
    stem = max(mine)  # the names sort as their times do
    # RULE: a start is not judged before its connectors had time to load
    if now - (_start_time(stem) or now) < LOAD_SECONDS:
        return "not yet"
    return not any(f.parent.name.startswith(CONNECTOR) for f in starts[stem])


async def proxy_answers() -> bool:
    """Whether claude.ai's connector proxy answers at all: any HTTP answer counts."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.head(PROXY)
    except httpx.HTTPError:
        return False
    return True


@dataclass
class Case:
    """One start of a session found without its connectors, and what was done about it."""

    start: Any  # the start's startedAt
    since: float  # when the bridge first saw it
    warned: bool = False  # told it is busy and will be restarted at rest
    told: bool = False  # told it can't be restarted, or that the restart didn't help
    tried: Any = None  # the start the bridge restarted
    broke: bool = False  # that restart failed outright


class Mender:
    """At each look, restarts the sessions that started without their claude.ai connectors,
    once each, at rest, and tells the session when it can't."""

    def __init__(
        self,
        live: Callable[[], list[dict[str, Any]]],
        missing: Callable[[dict[str, Any]], bool | None],
        started_here: Callable[[dict[str, Any]], bool],
        at_rest: Callable[[dict[str, Any]], bool],
        reachable: Callable[[], Awaitable[bool]],
        restart: Callable[[dict[str, Any]], Awaitable[None]],
        send: Callable[[dict[str, Any], str], Awaitable[Any]],
        record: Callable[[str, str, str], None],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.live, self.missing, self.started_here, self.at_rest = live, missing, started_here, at_rest
        self.reachable, self.restart, self.send, self.record, self.clock = reachable, restart, send, record, clock
        self.cases: dict[str, Case] = {}

    async def step(self) -> None:
        for d in self.live():
            try:
                await self._mend(d)
            except Exception as exc:  # one session's trouble never stops the others
                log.info("connectors check of %s failed: %s", d.get("name") or d.get("sessionId"), exc)

    async def _mend(self, d: dict[str, Any]) -> None:
        sid, start, name = str(d.get("sessionId")), d.get("startedAt"), str(d.get("name") or d.get("sessionId"))
        verdict = self.missing(d)
        if verdict is False:
            self.cases.pop(sid, None)  # it has them: a later start is judged afresh
            return
        if verdict is not True:
            return
        case = self.cases.get(sid)
        if case is None or (case.start != start and case.tried is None):
            case = self.cases[sid] = Case(start, self.clock())
        if case.tried is not None:
            # RULE: a restart that comes back without them is not tried again and the session is told
            if (case.tried != start or case.broke) and not case.told:
                case.told = True
                log.info("%s is still without its claude.ai connectors after the bridge's restart", name)
                self.record(name, "connectors_still_missing", f"{name}: the restart didn't bring its connectors back")
                await self.send(d, RESTART_FAILED_MESSAGE)
            return
        # RULE: a session the bridge did not start is told to warn the owner
        if not self.started_here(d):
            if not case.told:
                case.told = True
                log.info("%s has no claude.ai connectors; the bridge didn't start it, so it is told", name)
                self.record(name, "connectors_missing", f"{name}: no claude.ai connectors; not started by the bridge")
                await self.send(d, NOT_STARTED_MESSAGE)
            return
        # RULE: a busy session is not restarted mid turn
        if not self.at_rest(d):
            if not case.warned and self.clock() - case.since >= BUSY_WARNING_SECONDS:
                case.warned = True
                await self.send(d, BUSY_MESSAGE)
            return
        # RULE: a session is not restarted while claude.ai cannot be reached
        if not await self.reachable():
            return
        case.tried = start
        try:
            await self.restart(d)
        except Exception as exc:
            case.broke = True
            log.info("could not restart %s for its claude.ai connectors: %s", name, exc)
            return
        log.info("restarted %s: it had started without its claude.ai connectors", name)
        self.record(name, "connectors_restarted", f"{name}: restarted, it had no claude.ai connectors")
