"""Sessions stop_active_session parked rather than stopped, so the Claude app keeps them listed.

`claude stop` archives a session's claude.ai session, and the Claude app stops listing it. Claude
Code's own close of a session at rest keeps it listed, shown offline; nothing outside
Claude Code can ask for that close (Claude Code 2.1.325; ADR-CV-107). So stop_active_session leaves
an idle session on Remote Control at rest, "parked", for Claude Code to close, and this module keeps
the mark that lists it as parked until it works again.

The fork's own; it lives apart from upstream's files so a merge from upstream doesn't meet it there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from .store import Store

# With a parked session: "parked" is not what the owner expects back from "stop", and a relay
# may have read only the start of stop_active_session's description.
SAY = (
    "{name} is parked, not stopped: it stays in the owner's session list in the Claude app, at rest, "
    "until Claude Code closes it about 8 hours after it last worked (sooner when memory runs low). "
    "Once closed, the app still lists it, shown offline, but a message typed to it there waits "
    "unseen until the bridge resumes it. stop_active_session with now=true stops it at once and "
    "takes it off that list."
)
SETTING = "parked"  # session id -> when it was parked, in milliseconds
# Session id -> when the bridge left it down at its start because it was parked, stamped as
# Claude Code's daemon log stamps its lines (jobs.stamp).
DOWN = "left_down"
DOWN_WHY = "at the PC's shutdown, while parked"


def park(store: Store, sid: str, parked_at_ms: float | None) -> None:
    """Mark a session parked, at that time in milliseconds, or drop its mark (None)."""
    marks = store.get_setting(SETTING) or {}
    if parked_at_ms is not None:
        marks[sid] = parked_at_ms
    elif sid in marks:
        del marks[sid]
    else:
        return
    store.set_setting(SETTING, marks)


def parked(store: Store, d: dict[str, Any]) -> bool:
    """Whether a running session is one stop_active_session parked and that hasn't worked since.
    Claude Code's registry stamps statusUpdatedAt at each write that carries a status, even an
    unchanged one, so a rewrite (a respawn after a wake) also drops the mark: the label only."""
    sid = str(d.get("sessionId"))
    parked_at = (store.get_setting(SETTING) or {}).get(sid)
    if parked_at is None:
        return False
    # RULE: a session shows as parked only until it works again
    if d.get("status") != "idle" or (d.get("statusUpdatedAt") or 0) > parked_at:
        park(store, sid, None)
        return False
    return True


def prune(store: Store, running_ids: Iterable[str]) -> None:
    """Drop the marks of sessions no longer running."""
    marks = store.get_setting(SETTING) or {}
    running = set(running_ids)
    # RULE: a parked mark goes once its session is no longer running
    if marks.keys() - running:
        store.set_setting(SETTING, {sid: at for sid, at in marks.items() if sid in running})


def marks(store: Store) -> dict[str, float]:
    """Every parked mark: session id -> when it was parked, in milliseconds."""
    return dict(store.get_setting(SETTING) or {})


def left_parked(marked: Mapping[str, float], sid: str, last_write_ms: float | None) -> bool:
    """Whether a session was still parked when the bridge last ran: marked, and its conversation not
    written since (`last_write_ms`, None when there is none to read). The mark alone can be stale:
    it is dropped only when the sessions are listed. A shutdown writes nothing to an idle session's
    conversation (seen 2026-11-21), so the last write still tells."""
    parked_at = marked.get(sid)
    if parked_at is None:
        return False
    # RULE: a parked session that worked since is not counted as parked
    return last_write_ms is None or last_write_ms <= parked_at


def leave_down(store: Store, sid: str, at: str, spawned: Mapping[str, str]) -> None:
    """Mark a session the bridge left down at its start because it was parked, at `at` (jobs.stamp),
    and drop the marks of sessions Claude Code started again since (`spawned`, jobs.spawned)."""
    kept = _still_down(store.get_setting(DOWN) or {}, spawned)
    kept[sid] = at
    store.set_setting(DOWN, kept)


def left_down(store: Store, spawned: Mapping[str, str]) -> dict[str, str]:
    """The sessions the bridge left down at its start because they were parked, and that Claude Code
    hasn't started again since (`spawned`, jobs.spawned), by short id (a session id's first 8
    characters): when they were left down (jobs.stamp). Left down, one runs nowhere while the
    Claude app still lists it for the owner to type to, so it counts as closed (DOWN_WHY), like one
    Claude Code closed on its own (owner, 2026-11-21)."""
    return {sid[:8]: at for sid, at in _still_down(store.get_setting(DOWN) or {}, spawned).items()}


def _still_down(marks: Mapping[str, str], spawned: Mapping[str, str]) -> dict[str, str]:
    # RULE: a session left down stays down only until it runs again
    return {sid: at for sid, at in marks.items() if spawned.get(sid[:8], "") <= at}
