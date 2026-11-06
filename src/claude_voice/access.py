"""Which folders the bridge may reach: the root, folders the owner opened, never the closed ones.

See decisions/ADR-CV-006. A folder is "open" when it is the root or a granted folder, or
under one; "closed" when it is inside a closed folder, or holds one; otherwise it is
"not_open", and the owner can open it with request_access. One exception: a scratch folder
the Claude desktop app makes for a session opens even though it sits inside AppData.
"""

from __future__ import annotations

import fnmatch
import os
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # only for its type: access sits with the store, among the foundations
    from .store import Store

State = Literal["open", "not_open", "closed"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS access_grants(folder TEXT PRIMARY KEY, granted_at REAL NOT NULL);
"""

HOME_SECRETS = (".ssh", ".gnupg", ".claude", ".config/claude-voice", "AppData")


def default_denied(home: Path | None = None) -> list[Path]:
    """The folders kept closed: secrets under the home folder, and the system's own."""
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


def default_exempt(home: Path | None = None) -> list[Path]:
    """Patterns of folders opened even inside a closed one: the desktop app's scratch folders.

    Each is a path whose parts may hold wildcards; only a folder at exactly that depth, or
    inside one, matches. Everything above it, such as the app's own storage, stays closed.
    """
    home = Path(home) if home is not None else Path.home()
    return [
        home / "AppData" / "Local" / "Claude-Data" / "*" / "scratch-workspaces" / "*" / "*" / "scratch-*",
        home / "AppData" / "Roaming" / "Claude" / "scratch-workspaces" / "*" / "*" / "scratch-*",
    ]


def _under(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def _same(a: str, b: str) -> bool:
    return a.lower() == b.lower() if sys.platform == "win32" else a == b


def _matches(path: Path, pattern: Path) -> bool:
    """Whether `path` is the pattern's folder or inside it, one part per pattern part."""
    want = pattern.parts
    have = path.parts
    if len(have) < len(want):
        return False
    for part, glob in zip(have, want, strict=False):
        if any(c in glob for c in "*?["):
            if not fnmatch.fnmatch(part.lower(), glob.lower()):
                return False
        elif not _same(part, glob):
            return False
    return True


def _resolve_fixed_part(pattern: Path) -> Path:
    """Resolve a pattern up to its first wildcard, so it compares with resolved paths."""
    parts = pattern.parts
    first = next((i for i, part in enumerate(parts) if any(c in part for c in "*?[")), len(parts))
    return Path(*parts[:first]).resolve().joinpath(*parts[first:])


class Access:
    def __init__(self, store: Store, root: Path, denied: Iterable[Path], exempt: Iterable[Path] = ()) -> None:
        self.db = store.db
        self.db.executescript(SCHEMA)
        self.root = Path(root).resolve()
        self.denied = [Path(p).resolve() for p in denied]
        self.exempt = [_resolve_fixed_part(Path(p)) for p in exempt]

    def granted(self) -> list[Path]:
        return [Path(r[0]) for r in self.db.execute("SELECT folder FROM access_grants ORDER BY folder")]

    def base(self, path: str | Path) -> Path | None:
        """The open folder (root or grant) that holds `path`, if any."""
        p = Path(path).resolve()
        for folder in (self.root, *self.granted()):
            if _under(p, folder):
                return folder
        return None

    def _exempted(self, p: Path) -> bool:
        return any(_matches(p, pattern) for pattern in self.exempt)

    def closed_by(self, path: str | Path) -> tuple[str, Path] | None:
        """Why a folder is closed: ("inside", folder) or ("holds", folder); None if it isn't."""
        p = Path(path).resolve()
        inside = next((d for d in self.denied if _under(p, d)), None)
        # RULE: only a Desktop scratch folder is opened inside a denied folder
        if inside is not None and not self._exempted(p):
            return ("inside", inside)
        if inside is None and self.base(p) is None:
            held = next((d for d in self.denied if _under(d, p)), None)
            if held is not None:
                return ("holds", held)
        return None

    def state(self, path: str | Path) -> State:
        if self.closed_by(path) is not None:
            return "closed"
        return "open" if self.base(path) is not None else "not_open"

    def explain(self, path: str | Path) -> str:
        """A sentence naming the closed folder and how `path` relates to it."""
        why = self.closed_by(path)
        if why is None:
            return f"{Path(path).resolve()} is not closed"
        how, folder = why
        if how == "inside":
            return f"{Path(path).resolve()} is inside {folder}, which is kept closed, so it is never opened"
        return f"{Path(path).resolve()} holds {folder}, which is kept closed, so it is never opened"

    def grant(self, path: str | Path) -> Path:
        """Open a folder and everything under it, for good. Refuses a closed one."""
        p = Path(path).resolve()
        # RULE: a folder holding or inside a denied folder is never granted
        if self.state(p) == "closed":
            raise ValueError(self.explain(p))
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
            raise ValueError(f"{p} was not opened with request_access; health lists the opened folders")
        return p
