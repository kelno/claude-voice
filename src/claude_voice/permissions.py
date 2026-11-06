"""The permissions a conversation the bridge runs gets: the ones it has where the owner runs it.

The bridge should be invisible: a session behaves the same whether the owner drives it at
the PC, from the phone or through the voice relay, with the same permissions, freedoms and
stops. So a resumed conversation keeps its own permission mode, and the folders added to it
for that session, and a new one gets the mode set for new sessions.

Where the mode is recorded depends on where the conversation ran: the desktop app keeps it in
its own record of the session (an undocumented file, only read here, never written), a
terminal session in its transcript.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .sessions import Grant
from .transcripts import PROJECTS_DIR, SESSION_ID, project_key_for_directory

MODES = frozenset({"default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"})

_local = Path(os.environ.get("LOCALAPPDATA", "~/AppData/Local")).expanduser()
_roaming = Path(os.environ.get("APPDATA", "~/AppData/Roaming")).expanduser()
# The Microsoft Store build keeps its data under Claude-Data/claude_<id>, the other one under Claude.
DESKTOP_ROOTS = (_local / "Claude-Data", _roaming / "Claude")


def _desktop_record(session_id: str, roots: tuple[Path, ...]) -> dict[str, Any] | None:
    name = f"local_{session_id}.json"
    for root in roots:
        for pattern in (f"*/claude-code-sessions/*/*/{name}", f"claude-code-sessions/*/*/{name}"):
            for path in root.glob(pattern):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(record, dict):
                    return record
    return None


def _transcript_mode(session_id: str, cwd: str, projects_dir: Path) -> str | None:
    path = projects_dir / project_key_for_directory(cwd) / f"{session_id}.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    mode = None
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # a line still being written
        if isinstance(entry, dict) and entry.get("type") == "permission-mode":
            mode = entry.get("permissionMode")
    return mode


def session_grant(
    session_id: str | None,
    cwd: str,
    new_mode: str,
    desktop_roots: tuple[Path, ...] = DESKTOP_ROOTS,
    projects_dir: Path = PROJECTS_DIR,
) -> Grant:
    """The permission mode and added folders to run `session_id` with; `new_mode` when it has none."""
    # RULE: a resumed session keeps the permissions it has where the owner runs it
    if session_id and SESSION_ID.fullmatch(session_id):
        record = _desktop_record(session_id, desktop_roots)
        if record and record.get("permissionMode") in MODES:
            dirs = tuple(
                str(d)
                for update in record.get("sessionPermissionUpdates") or []
                if update.get("type") == "addDirectories" and update.get("destination") == "session"
                for d in update.get("directories") or []
            )
            return Grant(record["permissionMode"], dirs)
        mode = _transcript_mode(session_id, cwd, projects_dir)
        if mode in MODES:
            return Grant(str(mode))
    return Grant(new_mode)
