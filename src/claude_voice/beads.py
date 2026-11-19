"""A beads store's work, carried by fleet_recap (a fork feature: UPSTREAM.md).

beads is an issue tracker for agents; it writes every issue to an export file, one JSON object a
line. When sessions are coordinated through a beads store, a page that shows the sessions also
wants the work they are on: what waits on the owner, what is in progress, what just closed. So a
top-level `beads_export` in config.toml names that export file, and fleet_recap reads it on every
call: the page asking fleet_recap sees each change to the store as soon as beads writes it, with no
rebuild and no second tool to allow. The bridge only reads the file; beads stays the one writer.
"""

from __future__ import annotations

import json
import logging
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("claude_voice")

# How far back a closed issue is still listed: a day's history, enough to see what just finished
# without making every fleet_recap answer longer with the whole archive.
CLOSED_WITHIN_HOURS = 24
# Containers of other issues rather than work a session does.
SKIPPED_TYPES = ("epic",)


def configured(file: Path | None) -> Path | None:
    """The beads export config.toml names, if any.

    Args:
        file: The settings file, or None when there is none.

    Returns:
        The export's path; None without the file or the key. A settings file that can't be parsed
        is the rest of the config's problem: here it means no beads store.
    """
    if file is None or not file.is_file():
        return None
    try:
        raw = tomllib.loads(file.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return None
    named = raw.get("beads_export")
    return Path(str(named)).expanduser() if named else None


def _closed_at(issue: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(issue.get("closed_at")).replace("Z", "+00:00"))
    except ValueError:
        return None


def board(path: Path, now: datetime | None = None) -> dict[str, Any]:
    """What the beads store holds now: its open issues, and those closed lately, newest first.

    Args:
        path: The beads export file.
        now: The time "lately" is counted back from; the current time when left out.

    Returns:
        `exported_at` (when beads last wrote the file), `open`, `closed` and `closed_within_hours`;
        or only `error`, saying why the file couldn't be read, for whoever shows the board to say so.
    """
    now = now or datetime.now(UTC)
    try:
        text = path.read_text(encoding="utf-8")
        written = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("beads export %s could not be read (%s)", path, exc)
        return {"error": f"The beads export {path} could not be read: {exc}"}
    open_issues: list[dict[str, Any]] = []
    closed: list[dict[str, Any]] = []
    since = now - timedelta(hours=CLOSED_WITHIN_HOURS)
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            issue = json.loads(line)
        except json.JSONDecodeError as exc:
            log.warning("beads export %s, line %d, is not JSON (%s)", path, number, exc)
            return {"error": f"The beads export {path}, line {number}, is not JSON: {exc}"}
        if issue.get("issue_type") in SKIPPED_TYPES:
            continue
        if issue.get("status") != "closed":
            open_issues.append(
                {
                    "id": issue.get("id"),
                    "title": issue.get("title"),
                    "status": issue.get("status"),
                    "labels": issue.get("labels") or [],
                    "metadata": issue.get("metadata") or {},
                }
            )
            continue
        closed_at = _closed_at(issue)
        if closed_at is not None and closed_at >= since:
            closed.append(
                {
                    "id": issue.get("id"),
                    "title": issue.get("title"),
                    "close_reason": issue.get("close_reason") or "",
                    "closed_at": issue.get("closed_at"),
                }
            )
    closed.sort(key=lambda i: str(i["closed_at"]), reverse=True)
    return {
        "exported_at": written.isoformat(),
        "open": open_issues,
        "closed": closed,
        "closed_within_hours": CLOSED_WITHIN_HOURS,
    }
