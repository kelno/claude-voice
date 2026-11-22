"""The bridge's sessions kept in step with the Claude app, by the bridge itself (owner, 2026-11-22).

Archiving a session in the Claude app ends its Remote Control link, but the process keeps running
(ledger Q85, Q163): its live entry's bridgeSessionId turns null and Claude Code writes into its
conversation "Remote Control disconnected — this session was ended or archived from another device
or app (code 4090)" (a deletion says "... may have been deleted ... (code 4090)"). For the owner,
archiving means stopping: "Archive means stop at once" (owner, 2026-11-22). So a running background
session whose link is gone and whose conversation says it was archived since it started is stopped.
No link has been seen to drop without such a line, so nothing else here touches a session without one.

A session opened in the desktop app stays the desktop app's: resumed in place by the bridge, the app
refuses typing to it ("running in the background", ledger Q150). A copy under a new id, started the
bridge's way (`--resume <id> --fork-session`), takes typing in the app and has the whole history
(tried 2026-11-22). The owner doesn't use desktop sessions, and one that exists is a mistake, so once
one has been idle a few minutes the bridge copies it to itself. The bridge can't close the original
(only the app's Archive does), so the copy is remembered and the original is shown as copied, for the
owner to archive. A stopped conversation born in the desktop app is resumed the same way, as a copy.

The fork's own; it lives apart from upstream's files so a merge from upstream doesn't meet it there.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

COPIES = "desktop_copies"  # the store's setting: original session id -> {copy, name, at}
DESKTOP_IDLE_SECONDS = 300  # "a few minutes idle" before a desktop session is copied (owner)
ASIDE_PREFIX = "btw:"  # a desktop aside, opened from a desktop session: not copied
# Claude Code's words when the app archived or deleted a session (Claude Code 2.1.330).
APP_ENDED = ("Remote Control disconnected", "from another device or app (code 4090)")
TAIL_BYTES = 256 * 1024  # the end of a conversation is enough: the line comes as the archive happens


def job_record(jobs_dir: Path, sid: str) -> dict[str, Any] | None:
    """Claude Code's job record for the session `sid`, kept under its short id, or None."""
    try:
        record = json.loads((jobs_dir / sid[:8] / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) and record.get("sessionId") == sid else None


def _ms(stamp: str) -> float:
    """A conversation line's time stamp (2026-11-22T18:12:32.706Z), in milliseconds."""
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000


def archived_since(transcript: Path, started_ms: float) -> bool:
    """Whether a conversation says the Claude app archived or deleted its session since `started_ms`."""
    try:
        with transcript.open("rb") as f:
            f.seek(max(0, os.fstat(f.fileno()).st_size - TAIL_BYTES))
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return False
    for line in reversed(tail.splitlines()):
        if APP_ENDED[0] not in line:
            continue
        try:
            said = json.loads(line)
            content, at = str(said.get("content") or ""), _ms(str(said["timestamp"]))
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        # RULE: only an archive since the session started counts
        if content.startswith(APP_ENDED[0]) and content.endswith(APP_ENDED[1]) and at >= started_ms:
            return True
    return False


def _since(d: Mapping[str, Any]) -> float:
    """When a session's status last changed, in seconds."""
    return float(d.get("statusUpdatedAt") or 0) / 1000


def resumed_id(sid: str, copies: Mapping[str, Any]) -> str:
    """The conversation the bridge resumes when asked for `sid`: its copy, if it made one."""
    copy = copies.get(sid)
    # RULE: a conversation already copied to the bridge resumes its copy
    return str(copy["copy"]) if copy else sid


def how_to_resume(sid: str, was_background: bool, born_in: str | None, copies: Mapping[str, Any]) -> tuple[str, str]:
    """How the bridge resumes the stopped conversation `sid`: ("resume", the id to resume in place,
    which may be the copy already made of it) or ("copy", sid) to start it as a copy under a new id."""
    target = resumed_id(sid, copies)
    if target != sid:
        return "resume", target
    # RULE: a conversation born in the desktop app is resumed as a copy
    if born_in == "desktop" and not was_background:
        return "copy", sid
    return "resume", sid


class Keeper:
    """At each look: stops a background session the owner archived in the Claude app, and copies an
    idle desktop session to the bridge; each once, and what it does is recorded."""

    def __init__(
        self,
        live: Callable[[], list[dict[str, Any]]],
        job_record: Callable[[str], Mapping[str, Any] | None],
        archived: Callable[[dict[str, Any]], bool],
        stop: Callable[[dict[str, Any]], Awaitable[None]],
        copy: Callable[[dict[str, Any]], Awaitable[Mapping[str, Any]]],
        copies: Callable[[], Mapping[str, Any]],
        remember_copy: Callable[[str, dict[str, Any]], None],
        record: Callable[[str, str, str], None],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.live, self.job_record, self.archived, self.stop, self.copy = live, job_record, archived, stop, copy
        self.copies, self.remember_copy, self.record, self.clock = copies, remember_copy, record, clock
        self.stop_failed: set[tuple[str, Any]] = set()  # (session id, start) whose stop failed, recorded once
        self.failed: set[str] = set()  # desktop sessions whose copy failed, recorded once

    async def step(self) -> None:
        for d in self.live():
            try:
                if d.get("entrypoint") == "claude-desktop":
                    await self._copy(d)
                elif d.get("kind") == "bg":
                    await self._stop_archived(d)
            except Exception as exc:  # one session's trouble never stops the others
                log.info("keeping %s in step with the app failed: %s", d.get("name") or d.get("sessionId"), exc)

    async def _stop_archived(self, d: dict[str, Any]) -> None:
        sid, name = str(d.get("sessionId")), str(d.get("name") or d.get("sessionId"))
        if d.get("bridgeSessionId") or (sid, d.get("startedAt")) in self.stop_failed:
            return
        # RULE: a session archived in the Claude app is stopped at once
        if not self.archived(d):
            return
        try:
            await self.stop(d)
        except Exception as exc:
            self.stop_failed.add((sid, d.get("startedAt")))
            log.info("could not stop %s, archived in the Claude app: %s", name, exc)
            self.record(name, "stop_failed", f"{name}: archived in the Claude app; stopping it failed: {exc}")
            return
        log.info("stopped %s: it was archived in the Claude app", name)
        self.record(name, "stopped_archived", f"{name}: stopped, it was archived in the Claude app")

    async def _copy(self, d: dict[str, Any]) -> None:
        sid, name = str(d.get("sessionId")), str(d.get("name") or d.get("sessionId"))
        # RULE: a desktop session is copied to the bridge once
        if sid in self.copies() or sid in self.failed:
            return
        # RULE: a desktop aside is not copied
        if name.startswith(ASIDE_PREFIX):
            return
        # RULE: a desktop window on a conversation the bridge runs is not copied
        if self.job_record(sid) is not None:
            return
        # RULE: a desktop session is copied once it has been idle a few minutes
        if d.get("status") != "idle" or self.clock() - _since(d) < DESKTOP_IDLE_SECONDS:
            return
        try:
            made = await self.copy(d)
        except Exception as exc:
            self.failed.add(sid)
            log.info("could not copy the desktop session %s to the bridge: %s", name, exc)
            self.record(name, "copy_failed", f"{name}: a desktop session; copying it to the bridge failed: {exc}")
            return
        copy_name = str(made.get("name") or "")
        self.remember_copy(sid, {"copy": str(made.get("claude_session_id")), "name": copy_name, "at": self.clock()})
        log.info("copied the desktop session %s to the bridge as %s", name, copy_name)
        self.record(name, "copied_to_bridge", f"{name}: a desktop session, copied to the bridge as {copy_name}")
