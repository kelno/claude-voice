"""Sessions the bridge resumes whenever it starts, unless they already run (a fork feature: UPSTREAM.md).

The bridge wakes the sessions a shutdown interrupted, but only those it saw running at its last
look. At a shutdown, Windows may end a session a moment before the bridge, which then misses it
(seen 2026-11-21: the session that coordinates the owner's other sessions stayed down after an
update restart, and the owner couldn't reach it from the phone). Some sessions must always be up,
so config.toml names them: a top-level `wake_at_start`, a list of session names or ids. At each
start, after a boot or a restart of the bridge alone, the bridge resumes each one that isn't
running, in place with its whole history and its saved options (Remote Control included), the way
attach_conversation does; one already running is left alone. A name finds the newest session that
had it, so it still works when the conversation behind the name changes; an id never changes.
"""

from __future__ import annotations

import asyncio
import logging
import time
import tomllib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("claude_voice")

KEY = "wake_at_start"


def configured(file: Path | None) -> list[str]:
    """The sessions config.toml names to wake at start; none without the file or the list. A file
    that can't be parsed is the rest of the config's problem: here it means none."""
    if file is None or not file.is_file():
        return []
    try:
        raw = tomllib.loads(file.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return []
    named = raw.get(KEY)
    if not isinstance(named, list):
        return []
    return [str(n).strip() for n in named if str(n).strip()]


async def until_ready(ready: Callable[[], Awaitable[bool]], retry: float, patience: float) -> None:
    """Wait until claude.ai answers, asked every `retry` seconds, for up to `patience` seconds: a
    session started before the network is back runs without its claude.ai connectors."""
    deadline = time.monotonic() + patience
    # RULE: the sessions to wake wait until claude.ai answers
    while not await ready():
        if time.monotonic() >= deadline:
            log.info("claude.ai still doesn't answer after %d s; waking the sessions anyway", patience)
            return
        await asyncio.sleep(retry)


async def wake(
    wanted: list[str],
    running: Callable[[str], bool],
    known: Callable[[str], dict[str, Any] | None],
    resume: Callable[[dict[str, Any]], Awaitable[Any]],
    ready: Callable[[], Awaitable[bool]] | None = None,
    retry: float = 5.0,
    patience: float = 300.0,
) -> list[str]:
    """Resume each session `wanted` names that isn't running; returns the ids resumed.

    `running` says whether a session runs, by a name or id; `known` finds one that doesn't (its
    claude_session_id, name and cwd, as every_session.known gives it); `resume` continues it in
    place. A failure is logged and the others are still resumed."""
    todo: dict[str, dict[str, Any]] = {}
    for session in wanted:
        # RULE: a session named to wake at start that already runs is left alone
        if running(session):
            continue
        found = known(session)
        if found is None:
            log.warning("%s names %r, but no session the bridge knows has that name or id", KEY, session)
            continue
        sid = str(found["claude_session_id"])
        if running(sid):
            continue  # named by a name it had before, and running under its id
        todo.setdefault(sid, found)
    if todo and ready is not None:
        await until_ready(ready, retry, patience)
    woken = []
    for sid, found in todo.items():
        try:
            # RULE: a session named to wake at start is resumed when it is not running
            await resume(found)
        except Exception as exc:
            log.warning("could not wake %s (%s), named in %s: %s", found.get("name"), sid, KEY, exc)
            continue
        log.info("woke %s (%s), named in %s", found.get("name"), sid, KEY)
        woken.append(sid)
    return woken
