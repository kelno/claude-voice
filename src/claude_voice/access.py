"""Which folders the bridge may reach: the root, folders the owner opened, never the denied ones.

See decisions/ADR-CV-006. A folder is "allowed" when it is the root or a granted folder,
or under one; "blocked" when it is inside a denied folder, or holds one; otherwise it
"needs_approval", and the owner can grant it.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from .store import Store

State = Literal["allowed", "needs_approval", "blocked"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS access_grants(folder TEXT PRIMARY KEY, granted_at REAL NOT NULL);
"""

HOME_SECRETS = (".ssh", ".gnupg", ".claude", ".config/claude-voice", "AppData")


def default_denied(home: Path | None = None) -> list[Path]:
    """The folders no grant opens: secrets under the home folder, and the system's own."""
    home = Path(home) if home is not None else Path.home()
    denied = [home / name for name in HOME_SECRETS]
    if sys.platform == "win32":
        for var in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
            if os.environ.get(var):
                denied.append(Path(os.environ[var]))
    else:
        # Not /var: macOS keeps temporary folders under it.
        denied += [Path(p) for p in ("/etc", "/root", "/boot", "/usr", "/bin", "/sbin", "/System", "/Library")]
    return denied


def _under(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


class Access:
    def __init__(self, store: Store, root: Path, denied: Iterable[Path]) -> None:
        self.db = store.db
        self.db.executescript(SCHEMA)
        self.root = Path(root).resolve()
        self.denied = [Path(p).resolve() for p in denied]

    def granted(self) -> list[Path]:
        return [Path(r[0]) for r in self.db.execute("SELECT folder FROM access_grants ORDER BY folder")]

    def base(self, path: str | Path) -> Path | None:
        """The open folder (root or grant) that holds `path`, if any."""
        p = Path(path).resolve()
        for folder in (self.root, *self.granted()):
            if _under(p, folder):
                return folder
        return None

    def state(self, path: str | Path) -> State:
        p = Path(path).resolve()
        if any(_under(p, d) for d in self.denied):
            return "blocked"
        if self.base(p) is not None:
            return "allowed"
        if any(_under(d, p) for d in self.denied):
            return "blocked"
        return "needs_approval"

    def grant(self, path: str | Path) -> Path:
        """Open a folder and everything under it, for good. Refuses a blocked one."""
        p = Path(path).resolve()
        # RULE: a folder holding or inside a denied folder is never granted
        if self.state(p) == "blocked":
            raise ValueError(f"{p} is never opened: it is, or holds, a folder kept closed")
        if not p.is_dir():
            raise ValueError(f"not a directory: {p}")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO access_grants VALUES(?,?)", (str(p), time.time()))
        return p

    def revoke(self, path: str | Path) -> Path:
        p = Path(path).resolve()
        with self.db:
            gone = self.db.execute("DELETE FROM access_grants WHERE folder=?", (str(p),)).rowcount
        if not gone:
            raise ValueError(f"{p} was not granted; granted folders are listed by health")
        return p
