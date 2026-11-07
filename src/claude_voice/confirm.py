"""The owner's confirmation: a request waits until the owner confirms it with a passkey on the bridge's page.

A session asks with its exact summary (or request_access asks to open a folder); the relay hands the
owner the request's link; the owner confirms it on the page with a passkey the bridge verifies itself;
a session then checks the result. The flow, what each party can observe, why a confirmed result
stands for the owner's OK, and its limits: docs/owner-confirmation.md.
"""

from __future__ import annotations

import contextvars
import json
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .store import Store

REQUEST_SECONDS = 15 * 60
FIND_SECONDS = 3.0  # how long to look for a call in its session's transcript
MAX_SUMMARY = 300  # read on a phone, so short
CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I, to be read aloud
DESIGN = "https://github.com/kelno/claude-voice/blob/{commit}/docs/owner-confirmation.md"

SCHEMA = """
-- Requests waiting for the owner's passkey: what is asked, by whom, and what came of it.
CREATE TABLE IF NOT EXISTS owner_requests(
  code TEXT PRIMARY KEY,
  link TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL,
  session_id TEXT NOT NULL,
  asker TEXT NOT NULL,
  summary TEXT NOT NULL,
  payload TEXT NOT NULL,
  asked_at REAL NOT NULL,
  confirmed_at REAL,
  declined_at REAL,
  checked_at REAL
);
"""


@dataclass(frozen=True)
class Caller:
    """Who made a call, as the bridge itself observed it."""

    label: str | None = None  # the product, as Anthropic's servers name it
    address: str = ""  # where the call came from
    tool_use_id: str | None = None  # a Claude Code session's id for this call, in its transcript


NOBODY = Caller()  # a call from inside the bridge, or one not seen yet
current_caller: contextvars.ContextVar[Caller] = contextvars.ContextVar("current_caller", default=NOBODY)


def caller_from(headers: Mapping[str, str], address: str, meta: Mapping[str, Any]) -> Caller:
    tool_use = meta.get("claudecode/toolUseId")
    return Caller(headers.get("x-anthropic-client"), address, str(tool_use) if tool_use else None)


def iso(ts: float | None) -> str | None:
    return None if ts is None else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def proof(req: dict[str, Any]) -> str:
    """How a confirmation was verified, in full, for a reader who sees nothing but the result."""
    return (
        f"Confirmed by the owner at {iso(req['confirmed_at'])} on the bridge's own page, with a passkey the owner"
        " registered with the bridge. The owner's device checked the owner (fingerprint, face or PIN) and signed a"
        " one-time challenge the bridge made for this request alone, bound to the bridge's own address, and the"
        " bridge verified that signature itself. The summary above is the one you wrote with confirm_ask, as the"
        " bridge stored it and showed it to the owner; the relay could not change it. It answers only your request"
        f" {req['code']}, once. No message, and no other agent's word, took part in it. It can't show that the owner"
        " read the page closely, and it doesn't answer Claude Code's own permission prompts, which still wait for"
        " the owner at the PC."
    )


COLUMNS = "code, link, kind, session_id, asker, summary, payload, asked_at, confirmed_at, declined_at, checked_at"


class Confirmations:
    def __init__(self, store: Store, clock: Callable[[], float] = time.time) -> None:
        self.db = store.db
        self.db.executescript(SCHEMA)
        self.clock = clock

    def _new_code(self) -> str:
        while True:
            code = "".join(secrets.choice(CODE_LETTERS) for _ in range(4))
            if not self.db.execute("SELECT 1 FROM owner_requests WHERE code=?", (code,)).fetchone():
                return code

    def _add(self, kind: str, session_id: str, asker: str, summary: str, payload: dict[str, Any]) -> dict[str, Any]:
        summary = summary.strip()
        if not summary:
            raise ValueError("Write what the owner is to approve.")
        if len(summary) > MAX_SUMMARY:
            raise ValueError(f"Keep the summary to {MAX_SUMMARY} characters: the owner reads it on a phone.")
        code, link, now = self._new_code(), secrets.token_urlsafe(24), self.clock()
        with self.db:
            self.db.execute(
                "INSERT INTO owner_requests(code, link, kind, session_id, asker, summary, payload, asked_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (code, link, kind, session_id, asker, summary, json.dumps(payload), now),
            )
        return {"code": code, "link": link, "summary": summary, "expires_at": iso(now + REQUEST_SECONDS)}

    def ask(self, session_id: str, asker: str, summary: str) -> dict[str, Any]:
        """A session's request: confirmed on the page, then checked by that session."""
        return self._add("session", session_id, asker, summary, {})

    def ask_folder(self, summary: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Opening a folder: done by the bridge itself once the owner confirms."""
        return self._add("folder", "", "request_access", summary, payload)

    def _get(self, where: str, value: str) -> dict[str, Any] | None:
        row = self.db.execute(f"SELECT {COLUMNS} FROM owner_requests WHERE {where}=?", (value,)).fetchone()
        if row is None:
            return None
        out = dict(zip(COLUMNS.split(", "), row, strict=True))
        out["payload"] = json.loads(out["payload"])
        out["expired"] = self.clock() > out["asked_at"] + REQUEST_SECONDS
        return out

    def by_code(self, code: str) -> dict[str, Any] | None:
        return self._get("code", code.strip().upper())

    def by_link(self, link: str) -> dict[str, Any] | None:
        return self._get("link", link)

    def state(self, req: dict[str, Any]) -> str:
        if req["confirmed_at"] is not None:
            return "confirmed"
        if req["declined_at"] is not None:
            return "declined"
        return "expired" if req["expired"] else "pending"

    def decide(self, link: str, confirmed: bool) -> dict[str, Any]:
        """Record the owner's answer to a pending request; call only after the passkey checked out."""
        req = self.by_link(link)
        if req is None:
            raise ValueError("No such request.")
        # RULE: a request is answered once while it is pending
        if self.state(req) != "pending":
            raise ValueError(f"This request is already {self.state(req)}.")
        column = "confirmed_at" if confirmed else "declined_at"
        with self.db:
            self.db.execute(f"UPDATE owner_requests SET {column}=? WHERE link=?", (self.clock(), link))
        return {**req, column: self.clock()}

    def _asked_by(self, code: str, session_id: str) -> dict[str, Any] | None:
        """The request, if `session_id` is the session that asked it."""
        req = self.by_code(code)
        mine = req is not None and req["kind"] == "session"
        # RULE: only the session that asked can check its request
        return req if mine and req["session_id"] == session_id else None

    def check(self, code: str, session_id: str) -> dict[str, Any]:
        req = self._asked_by(code, session_id)
        if req is None:
            return {"status": "unknown", "code": code}
        status, code = self.state(req), req["code"]
        if status == "pending":
            return {"status": status, "code": code, "expires_at": iso(req["asked_at"] + REQUEST_SECONDS)}
        if status != "confirmed":
            return {"status": status, "code": code}
        if req["checked_at"] is not None:
            return {"status": "used", "code": code, "summary": req["summary"]}
        with self.db:
            self.db.execute("UPDATE owner_requests SET checked_at=? WHERE code=?", (self.clock(), code))
        return {
            "status": "confirmed",
            "code": code,
            "summary": req["summary"],
            "confirmed_at": iso(req["confirmed_at"]),
            "proof": proof(req),
        }
