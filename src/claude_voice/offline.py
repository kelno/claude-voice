"""The sessions the Claude app shows offline, so a relay can tell the owner and resume them.

Claude Code closes a background session on its own once it has sat idle (jobs.closed_by_claude_code).
A session on Remote Control then stays in the Claude app's list, shown offline, and the app still
lets the owner type to it. That message waits on Anthropic's side until the session runs again:
nothing on this computer sees it, and nothing wakes the session for it (Claude Code 2.1.325;
ledger Q113). So the bridge names these sessions, and a relay resumes one the owner wrote to.

The fork's own; it lives apart from upstream's files so a merge from upstream doesn't meet it there.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import app_links

if TYPE_CHECKING:
    from .jobs import Closed

# How far back a close is still worth naming: the owner writes to a session they worked with lately.
RECENT = timedelta(days=3)


def _when(stamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def shown_offline(
    closed_now: dict[str, Closed],
    jobs_dir: Path,
    now: datetime | None = None,
    link: Callable[[Any], str | None] = lambda _: None,
) -> list[dict[str, Any]]:
    """Of the sessions Claude Code closed on its own (jobs.closed_by_claude_code), those closed lately
    that are on Remote Control, latest close first: their id, name, folder, why they were closed
    and when, and the link that opens them in the app, made by `link` from their Remote Control id."""
    now = now or datetime.now(UTC)
    found = []
    for short, closed in closed_now.items():
        at = _when(closed.at)
        if at is None or now - at > RECENT:
            continue
        try:
            record = json.loads((jobs_dir / short / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        # RULE: only a session on Remote Control is shown offline in the Claude app
        if not isinstance(record, dict) or not record.get("bridgeSessionId"):
            continue
        found.append(
            {
                "claude_session_id": record.get("sessionId"),
                "name": record.get("name"),
                "folder": record.get("cwd"),
                "closed": closed.why,
                "closed_at": closed.at,
                **app_links.field(link(app_links.from_job(record.get("bridgeSessionId")))),
            }
        )
    return sorted(found, key=lambda s: str(s["closed_at"]), reverse=True)
