"""The link that opens a session in the Claude app, given wherever the bridge names a session.

Claude Code records a session's Remote Control id twice: as session_… in its live entry while it
runs, and as cse_… in its job record, kept once it has closed; the app's link takes the session_…
form (`server.app_url`). Agents used to build the link by hand from the job record, and every new
session had to be taught how; the bridge gives it instead, as app_url.

The fork's own; it lives apart from upstream's files so a merge from upstream doesn't meet it there.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

# How long a start or resume waits for Remote Control to give the session its id: Claude Code lists
# a new session first, and the id followed 1.3 s later when measured on 2026-11-19.
WAIT_SECONDS = 5.0


def field(url: str | None) -> dict[str, str]:
    """app_url for a result, only when there is a link: a session without one shows nothing."""
    return {"app_url": url} if url else {}


def from_job(bridge_session_id: Any) -> str | None:
    """The Remote Control id as the app's link takes it, from a job record's cse_… form."""
    if not isinstance(bridge_session_id, str) or not bridge_session_id.startswith("cse_"):
        return None
    return "session_" + bridge_session_id.removeprefix("cse_")


async def remote_id(entry_now: Callable[[], dict[str, Any] | None], expected: bool) -> str | None:
    """A just started or resumed session's Remote Control id, waiting up to WAIT_SECONDS for it
    when the session should have one; None when it doesn't come, or isn't expected."""
    deadline = time.monotonic() + (WAIT_SECONDS if expected else 0.0)
    while True:
        entry = entry_now() or {}
        if entry.get("bridgeSessionId"):
            return str(entry["bridgeSessionId"])
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(0.1)
