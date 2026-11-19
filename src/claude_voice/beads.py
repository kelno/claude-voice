"""A beads store's work, carried by fleet_recap on request (a fork feature: UPSTREAM.md).

beads is an issue tracker for agents; it writes every issue to an export file, one JSON object a
line. When sessions are coordinated through a beads store, a page that shows the sessions also
wants the work they are on: what waits on the owner, what is in progress, what just closed. So a
top-level `beads_export` in config.toml names that export file, and fleet_recap called with
`beads` true reads it: the page polling fleet_recap sees each change to the store as soon as beads
writes it, with no rebuild and no second tool to allow. Other calls leave it out, so a relay asking
what the sessions are up to doesn't read the store's history. The bridge only reads the file;
beads stays the one writer.
"""

from __future__ import annotations

import json
import logging
import os
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("claude_voice")

# How far back a closed issue is still listed: a day's history, enough to see what just finished.
CLOSED_WITHIN_HOURS = 24
# Containers of other issues rather than work a session does.
SKIPPED_TYPES = ("epic",)
# What fleet_recap says when asked for beads while none is set, for the asker to pass on.
NOT_SET = "The bridge has no beads store: its config.toml names no beads_export."


class Unreadable(ValueError):
    """An export the bridge could not read as beads issues."""


def configured(file: Path | None) -> Path | None:
    """The beads export config.toml names, if any.

    Args:
        file: The settings file, or None when there is none.

    Returns:
        The export's path, a relative one taken from the settings file's folder; None without the
        file or the key. A settings file that can't be parsed is the rest of the config's problem:
        here it means no beads store.
    """
    if file is None or not file.is_file():
        return None
    try:
        raw = tomllib.loads(file.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return None
    named = raw.get("beads_export")
    return file.parent / Path(str(named)).expanduser() if named else None


def _read(path: Path) -> tuple[str, datetime]:
    """The export's text, and when beads wrote that text, both from the same open file.

    beads writes a new export to a temporary file and renames it over this one. On Windows that
    rename fails while the file is open here, even opened with delete sharing, and beads then
    drops that export with only a warning: the store's change stays out of the file until beads
    writes it again (seen with bd 1.3.4). So the file is held open only for one read.
    """
    with open(path, "rb") as f:
        written = datetime.fromtimestamp(os.fstat(f.fileno()).st_mtime, UTC)
        return f.read().decode("utf-8"), written


def _issues(text: str) -> list[dict[str, Any]]:
    issues = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            issue = json.loads(line)
        except json.JSONDecodeError as exc:
            raise Unreadable(f"line {number} is not JSON: {exc}") from exc
        if not isinstance(issue, dict):
            raise Unreadable(f"line {number} is not an issue: {line[:80]}")
        issues.append(issue)
    return issues


def _closed_at(issue: dict[str, Any]) -> datetime | None:
    """When the issue was closed; a time without an offset is taken as UTC, as beads writes it."""
    try:
        at = datetime.fromisoformat(str(issue.get("closed_at")).replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


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
        text, written = _read(path)
        issues = _issues(text)
    except (OSError, UnicodeDecodeError, Unreadable) as exc:
        log.warning("beads export %s could not be read (%s)", path, exc)
        return {"error": f"The beads export {path} could not be read: {exc}"}
    open_issues: list[dict[str, Any]] = []
    closed: list[tuple[datetime, dict[str, Any]]] = []
    since = now - timedelta(hours=CLOSED_WITHIN_HOURS)
    for issue in issues:
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
            entry = {
                "id": issue.get("id"),
                "title": issue.get("title"),
                "close_reason": issue.get("close_reason") or "",
                "closed_at": issue.get("closed_at"),
            }
            closed.append((closed_at, entry))
    closed.sort(key=lambda pair: pair[0], reverse=True)
    return {
        "exported_at": written.isoformat(),
        "open": open_issues,
        "closed": [entry for _, entry in closed],
        "closed_within_hours": CLOSED_WITHIN_HOURS,
    }
