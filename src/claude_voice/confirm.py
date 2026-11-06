"""The owner's confirmation: a session asks, the owner taps the app's card in a voice chat, the session checks.

The flow, what each party can observe, why a confirmed result stands for the owner's OK, and its
limits: docs/owner-confirmation.md.
"""

from __future__ import annotations

import contextvars
import ipaddress
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .store import Store

# Where Anthropic's servers call connectors from (platform.claude.com/docs/en/api/ip-addresses).
ANTHROPIC_OUTBOUND = (ipaddress.ip_network("160.79.104.0/21"), ipaddress.ip_network("153.61.208.0/20"))
# The label Anthropic's servers send with a call from a claude.ai voice chat (x-anthropic-client).
VOICE_CHAT = "ClaudeAI"
REQUEST_SECONDS = 15 * 60
FIND_SECONDS = 3.0  # how long to look for a call in its session's transcript
MAX_SUMMARY = 300  # the card may cut a long one, and a short one gets read
CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I, to be read aloud
DESIGN = "https://github.com/kelno/claude-voice/blob/{commit}/docs/owner-confirmation.md"
# What a confirmed result can't show, said with every one.
LIMIT = (
    "A tap only if the owner keeps confirm_with_owner set to ask each time in the Claude app;"
    " set to always allow, the call goes through with no card, and the bridge can't tell."
)

SCHEMA = """
-- Requests for the owner's confirmation: who asked, what, and when it was confirmed and checked.
CREATE TABLE IF NOT EXISTS confirmations(
  code TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  summary TEXT NOT NULL,
  asked_at REAL NOT NULL,
  confirmed_at REAL,
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


def through_anthropic(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in net for net in ANTHROPIC_OUTBOUND)


def from_a_voice_chat(caller: Caller) -> bool:
    """Whether a call can stand for the owner's tap: only a voice chat shows the owner a card."""
    # RULE: only a voice chat through anthropic may confirm
    return caller.label == VOICE_CHAT and through_anthropic(caller.address)


def same_words(asked: str, given: str) -> bool:
    # RULE: the summary must match except for spaces
    return " ".join(asked.split()) == " ".join(given.split())


def iso(ts: float | None) -> str | None:
    return None if ts is None else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class Confirmations:
    def __init__(self, store: Store, clock: Callable[[], float] = time.time) -> None:
        self.db = store.db
        self.db.executescript(SCHEMA)
        self.clock = clock

    def _new_code(self) -> str:
        while True:
            code = "".join(secrets.choice(CODE_LETTERS) for _ in range(4))
            if not self.db.execute("SELECT 1 FROM confirmations WHERE code=?", (code,)).fetchone():
                return code

    def ask(self, session_id: str, summary: str) -> dict[str, Any]:
        summary = summary.strip()
        if not summary:
            raise ValueError("Write what the owner is to approve.")
        if len(summary) > MAX_SUMMARY:
            raise ValueError(f"Keep the summary to {MAX_SUMMARY} characters: the card may cut a longer one.")
        code, now = self._new_code(), self.clock()
        with self.db:
            self.db.execute(
                "INSERT INTO confirmations(code, session_id, summary, asked_at) VALUES(?,?,?,?)",
                (code, session_id, summary, now),
            )
        return {"code": code, "summary": summary, "expires_at": iso(now + REQUEST_SECONDS)}

    def _row(self, code: str) -> tuple[str, str, float, float | None, float | None] | None:
        row = self.db.execute(
            "SELECT session_id, summary, asked_at, confirmed_at, checked_at FROM confirmations WHERE code=?",
            (code.strip().upper(),),
        ).fetchone()
        return tuple(row) if row else None  # type: ignore[return-value]

    def confirm(self, code: str, summary: str) -> dict[str, Any]:
        """Record the owner's confirmation; returns the session that asked."""
        row = self._row(code)
        if row is None:
            raise ValueError(f"No request {code}: ask the session for its code.")
        session_id, asked, asked_at, confirmed_at, _ = row
        if confirmed_at is not None:
            raise ValueError(f"Request {code} is already confirmed.")
        if self.clock() > asked_at + REQUEST_SECONDS:
            raise ValueError(f"Request {code} expired: the session asks again.")
        if not same_words(asked, summary):
            raise ValueError(f"The summary isn't exactly the session's; copy it again, word for word: {asked}")
        with self.db:
            self.db.execute("UPDATE confirmations SET confirmed_at=? WHERE code=?", (self.clock(), code.upper()))
        return {"status": "confirmed", "code": code.upper(), "session_id": session_id}

    def check(self, code: str, session_id: str) -> dict[str, Any]:
        row = self._row(code)
        # RULE: only the session that asked can check its request
        if row is None or row[0] != session_id:
            return {"status": "unknown", "code": code}
        _, summary, asked_at, confirmed_at, checked_at = row
        if confirmed_at is None:
            if self.clock() > asked_at + REQUEST_SECONDS:
                return {"status": "expired", "code": code.upper()}
            return {"status": "pending", "code": code.upper(), "expires_at": iso(asked_at + REQUEST_SECONDS)}
        if checked_at is not None:
            return {"status": "used", "code": code.upper(), "summary": summary}
        with self.db:
            self.db.execute("UPDATE confirmations SET checked_at=? WHERE code=?", (self.clock(), code.upper()))
        return {
            "status": "confirmed",
            "code": code.upper(),
            "summary": summary,
            "confirmed_at": iso(confirmed_at),
            "limit": LIMIT,
        }
