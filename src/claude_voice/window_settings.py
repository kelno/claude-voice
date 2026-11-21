"""The settings of a session the bridge opens in a window, handed over in a file rather than inline.

The bridge gives Claude Code its settings as JSON on the command line (`--settings`). In the
background Claude Code gets that argument as it is. A window goes through a terminal, which on
Windows reads its own command line and builds the one it starts again: Windows Terminal drops
the escaped quotes inside the JSON, and Claude Code, given broken settings, exits at once (seen
2026-11-21). `--settings` takes a file too, and a path has no quotes to lose.

The fork's own; it lives apart from upstream's files so a merge from upstream doesn't meet it there.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

# One file per session name, written again at each start in a window.
FOLDER = Path(tempfile.gettempdir()) / "claude-voice-window-settings"


def in_a_file(command: list[str], name: str, folder: Path | None = None) -> list[str]:
    """`command` with its --settings JSON written to a file named after the session `name`, and
    that file's path in its place."""
    if "--settings" not in command:
        return command
    at = command.index("--settings") + 1
    folder = folder or FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{re.sub(r'[^A-Za-z0-9._-]+', '-', name).strip('-') or 'session'}.json"
    path.write_text(command[at], encoding="utf-8")
    # RULE: a session in a window gets its settings from a file
    return [*command[:at], str(path), *command[at + 1 :]]
