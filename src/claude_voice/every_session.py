"""The bridge handles every session, the ones the owner opened as well as the ones it started.

Upstream acts on some things only for a session the bridge started itself: stopping it, waking it
after a shutdown, resuming it when messaged, renaming it in Claude Code, restarting it without its
claude.ai connectors, compacting it. The owner has one session coordinate all the others, so the
fork does these for any session where it can (2026-11-17, ADR-CV-108). What still limits it is the
kind of session: Claude Code's background service renames, restarts, compacts and wakes only its
background sessions, and nothing outside the Claude desktop app ends one of the app's sessions
cleanly.

The fork's own; it lives apart from upstream's files so a merge from upstream doesn't meet it there.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from .store import Store

# What ends each kind cleanly is known only for these; a program's own session (Claude Code's SDK)
# ends with that program, and ending its process from outside would break it.
TERMINAL_ENTRYPOINTS = (None, "cli")
# The desktop app restarts a session whose process ends while idle, or shows an error card, and
# offers nothing outside the app that closes one (desktop app 5.11492, read 2026-11-17).
DESKTOP_WHY = (
    "it runs in the Claude desktop app, which starts it again or shows an error when its process is"
    " ended from outside; the owner closes it there"
)


def refusal(d: dict[str, Any], folder_open: bool) -> dict[str, Any] | None:
    """Why the bridge won't stop this running session, as fields of the result; None when it may."""
    # RULE: a session in a folder not open to the bridge is not stopped
    if not folder_open:
        return {"status": "folder_not_open"}
    # RULE: only a terminal or background session is stopped
    if d.get("kind") != "bg" and d.get("entrypoint") not in TERMINAL_ENTRYPOINTS:
        if d.get("entrypoint") == "claude-desktop":
            why = DESKTOP_WHY
        else:
            why = f"it belongs to a program using Claude Code's SDK ({d.get('entrypoint')}), which ends it itself"
        return {"status": "not_stoppable", "why": why}
    # RULE: any session in a folder open to the bridge can be stopped, the owner's own too
    return None


def known(store: Store, session: str, jobs_dir: Path) -> dict[str, Any] | None:
    """A session that isn't running, by its id, or by its name as a relay says it (in other capitals,
    without the asterisk, or a name it had before): the newest that had it, so a name used again
    finds the conversation behind it now, not an older one that once had it exactly. From the
    bridge's own record of what it started and from Claude Code's job records, which every
    background session has. Same shape as Store.started_session."""
    from . import renames  # a part beside this one: imported when needed, not when loaded

    old = store.old_names()
    # RULE: a background session the owner started is known from its job record too
    sessions = [*_started(store), *_job_records(jobs_dir)]
    # RULE: a session not running is found by the newest that had its name
    for _, found in sorted(sessions, key=lambda pair: pair[0], reverse=True):
        sid = found["claude_session_id"]
        row = {"sessionId": sid, "name": found["name"], "aliases": old.get(sid, [])}
        if renames.called(row, session, old_names=True, any_case=True):
            return found
    return None


def _started(store: Store) -> list[tuple[float, dict[str, Any]]]:
    """Every session the bridge started, with when, as Store.started_session gives one."""
    rows = store.db.execute("SELECT claude_session_id, name, cwd, started_at FROM started").fetchall()
    return [(float(r[3]), {"claude_session_id": str(r[0]), "name": str(r[1]), "cwd": str(r[2])}) for r in rows]


def _when(stamp: Any) -> float:
    """A job record's updatedAt, in seconds; 0 when it has none that reads."""
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _job_records(jobs_dir: Path) -> list[tuple[float, dict[str, Any]]]:
    """Every background session with a job record, with when the record last changed."""
    out = []
    for path in jobs_dir.glob("*/state.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sid, name, cwd = record.get("sessionId"), record.get("name"), record.get("cwd")
        if sid and cwd:
            found = {"claude_session_id": str(sid), "name": str(name or sid), "cwd": str(cwd)}
            out.append((_when(record.get("updatedAt")), found))
    return out


def job_records(jobs_dir: Path) -> list[dict[str, Any]]:
    """The background sessions Claude Code has a job record for, oldest first, as
    Store.started_session gives one."""
    return [k for _, k in sorted(_job_records(jobs_dir), key=lambda pair: pair[0])]
