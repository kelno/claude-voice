"""The running sessions as the bridge last saw them, and what changed between looks.

A LiveWatcher looks at the running Claude Code sessions every few seconds and keeps the last
look in the database, which tells the bridge, once restarted, what a shutdown interrupted. It
also records status changes (finished, waiting for input, started, ended) as feed entries.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import procs
from .store import Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_snapshot(name TEXT PRIMARY KEY, status TEXT, session_id TEXT);
CREATE TABLE IF NOT EXISTS feed(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  session TEXT NOT NULL,
  kind TEXT NOT NULL,
  text TEXT,
  project TEXT
);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""


def read_live(live_dir: Path) -> list[dict[str, Any]]:
    """Claude Code's registry of running sessions, keeping only live processes."""
    out = []
    for f in sorted(live_dir.glob("*.json")) if live_dir.is_dir() else []:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if not procs.alive(int(d["pid"])):
                continue  # the process is gone
        except (ValueError, KeyError, TypeError, OSError):
            continue  # unreadable
        out.append(d)
    return out


class LiveWatcher:
    def __init__(
        self,
        store: Store,
        live_dir: Path,
        root: Path,
        clock: Callable[[], float] = time.time,
        project_of: Callable[[str | None], str | None] | None = None,
    ) -> None:
        self.db = store.db
        self.db.executescript(SCHEMA)
        self.live_dir = live_dir
        self.root = Path(root).resolve()
        self.clock = clock
        self.project_of = project_of  # the bridge's own rule, granted folders included

    def _project(self, cwd: str | None) -> str | None:
        if self.project_of is not None:
            return self.project_of(cwd)
        if not cwd:
            return None
        path = Path(cwd).resolve()
        if path != self.root and self.root not in path.parents:
            return None
        return str(path.relative_to(self.root)) or "."

    def last_seen_live(self) -> set[str]:
        """The ids of the sessions running at the last look, kept across restarts."""
        return {str(r[0]) for r in self.db.execute("SELECT session_id FROM live_snapshot") if r[0]}

    def check(self) -> list[dict[str, Any]]:
        """Diff the running sessions against the last look; record and return the changes."""
        now = {}
        for d in read_live(self.live_dir):
            project = self._project(d.get("cwd"))
            if project is not None and d.get("name"):
                now[d["name"]] = (d.get("status"), d.get("sessionId"), project)

        before = {r[0]: (r[1], r[2]) for r in self.db.execute("SELECT * FROM live_snapshot").fetchall()}
        baselined = self.db.execute("SELECT 1 FROM meta WHERE key='baselined'").fetchone()

        changes: list[tuple[str, str, str | None, str | None]] = []
        if baselined:
            for name, (status, _, project) in now.items():
                if name not in before:
                    changes.append((name, "started", status, project))
                elif before[name][0] != status:
                    kind = _transition(before[name][0], status)
                    if kind:
                        changes.append((name, kind, status, project))
            for name in before.keys() - now.keys():
                changes.append((name, "ended", None, None))

        ts = self.clock()
        with self.db:
            self.db.execute("DELETE FROM live_snapshot")
            self.db.executemany(
                "INSERT INTO live_snapshot VALUES(?,?,?)",
                [(n, s, sid) for n, (s, sid, _) in now.items()],
            )
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('baselined','1')")
            seqs: list[int] = []
            for name, kind, _status, project in changes:
                cur = self.db.execute(
                    "INSERT INTO feed(ts, session, kind, text, project) VALUES(?,?,?,?,?)",
                    (ts, name, kind, _live_sentence(name, kind, project), project),
                )
                seqs.append(cur.lastrowid or 0)
        return self.feed_after(min(seqs) - 1) if seqs else []

    def record(self, session: str, kind: str, text: str | None) -> None:
        """Add a news item the watcher did not see itself, e.g. the tree changing shape."""
        with self.db:
            self.db.execute(
                "INSERT INTO feed(ts, session, kind, text, project) VALUES(?,?,?,?,?)",
                (self.clock(), session, kind, text, None),
            )

    def feed_after(self, seq: int, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT seq, ts, session, kind, text, project FROM feed WHERE seq>? ORDER BY seq LIMIT ?",
            (seq, limit),
        ).fetchall()
        return [{"seq": r[0], "ts": r[1], "session": r[2], "kind": r[3], "text": r[4], "project": r[5]} for r in rows]

    def last_seq(self) -> int:
        return self.db.execute("SELECT COALESCE(MAX(seq), 0) FROM feed").fetchone()[0]


def _live_sentence(name: str, kind: str, project: str | None) -> str:
    return {
        "finished": f"{name} has finished and is waiting.",
        "needs_input": f"{name} needs your input.",
        "working": f"{name} is working.",
        "started": f"{name} started in {project}.",
        "ended": f"{name} has ended.",
    }.get(kind, f"{name}: {kind}.")


def _transition(old: str | None, new: str | None) -> str | None:
    if new == "waiting":
        return "needs_input"
    if old in ("busy", "shell") and new == "idle":
        return "finished"
    if old in ("idle", "waiting") and new == "busy":
        return "working"
    return None
