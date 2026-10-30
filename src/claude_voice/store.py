"""The bridge's durable state (SQLite): the background sessions it started.

Other parts keep their own tables in the same database through `Store.db`.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCHEMA = """
-- Background sessions this bridge started; only these may it stop.
CREATE TABLE IF NOT EXISTS started(
  claude_session_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  cwd TEXT NOT NULL,
  started_at REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time) -> None:
        if str(path) != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            path = Path(path).expanduser()
        self.clock = clock
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def record_started(self, claude_session_id: str, name: str, cwd: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO started VALUES (?, ?, ?, ?)", (claude_session_id, name, cwd, self.clock())
            )

    def was_started(self, claude_session_id: str) -> bool:
        row = self.db.execute("SELECT 1 FROM started WHERE claude_session_id = ?", (claude_session_id,)).fetchone()
        return row is not None

    def started_session(self, name_or_id: str) -> dict[str, Any] | None:
        """A session the bridge started, by its Claude session id or name; the newest if names repeat."""
        row = self.db.execute(
            "SELECT claude_session_id, name, cwd FROM started WHERE claude_session_id = ? OR name = ? "
            "ORDER BY rowid DESC LIMIT 1",
            (name_or_id, name_or_id),
        ).fetchone()
        return dict(row) if row else None
