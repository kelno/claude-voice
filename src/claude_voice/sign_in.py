"""Claude Code's sign-in, named where it explains a failure: a delivery the courier didn't make,
and the bridge's health.

Once the sign-in has run out, running sessions stop taking messages, yet the courier's error said
only that it did not send the message and health showed the courier warm: the cause turned up only
in a fresh session's own reply, after several tries (ledger F72). The date comes from the same
place as the work page's warning (`work.read_login_expiry`, `claude auth status`).

The fork's own module, kept out of upstream's server.py and work.py so merges from upstream don't
meet it; server.py only hooks it in.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

EXPIRED = "Claude Code's sign-in has run out: run claude on the PC and type /login, then send again."
# A sign-in made elsewhere can end this PC's without its date passing (2026-11-12), so the date
# can't rule it out; a session's own answer shows it.
MAYBE_ENDED = (
    'If sessions answer "Login expired", Claude Code\'s sign-in has ended even though its date '
    "hasn't passed: run claude on the PC and type /login."
)


class Look:
    """The latest look at when the sign-in runs out, kept for health. Each `read` asks Claude
    Code, which can take seconds: call it off the event loop."""

    def __init__(
        self,
        ask: Callable[[], datetime | None],
        warning: timedelta,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.ask, self.warning, self.now = ask, warning, now
        self.expires: datetime | None = None
        self.looked_at: datetime | None = None

    def read(self) -> datetime | None:
        """When the sign-in runs out, asked now; None when Claude Code can't say."""
        self.expires, self.looked_at = self.ask(), self.now()
        return self.expires

    def state(self) -> dict[str, Any]:
        """The sign-in as health shows it, from the latest look: "signed_in", "running_out" (within
        the work page's warning), "expired" or "unknown"."""
        # RULE: health shows the sign-in state
        if self.expires is None:
            state = "unknown"
        elif self.expires <= self.now():
            state = "expired"
        else:
            state = "running_out" if self.expires - self.now() <= self.warning else "signed_in"
        return {
            "state": state,
            "expires_at": self.expires.isoformat(timespec="seconds") if self.expires else None,
            "looked_at": self.looked_at.isoformat(timespec="seconds") if self.looked_at else None,
        }

    def undelivered(self, detail: str) -> str:
        """The error for a message that wasn't delivered, naming the sign-in when it has run out:
        asked afresh, since a failure is rare and the hourly look may be stale. Otherwise it says
        how to tell an ended sign-in, which the date can't."""
        said = f"Could not deliver: {detail}"
        expires = self.read()
        # RULE: a failed delivery names a sign-in that has run out
        if expires is not None and expires <= self.now():
            return f"{said}. {EXPIRED}"
        # RULE: a failed delivery says how to spot an ended sign-in
        return f"{said}. {MAYBE_ENDED}"
