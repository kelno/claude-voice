"""Relay handles: which relay is calling, so each keeps its own read marks and followed sessions.

The owner often has two or more voice conversations open at once, and each must hear only of its
own sessions. Nothing a call carries identifies its conversation for long: a voice call carries an
MCP session id, but that id changes at every bridge restart, and the same chat continued by typing
carries none. So the conversation names itself, with a handle the bridge hands out (checked with the
owner, 2026-11-14).

Each handle keeps the time it was last used and its marks, one per Claude session, by session id;
what a mark holds and means is in following.py. A handle the bridge handed out also keeps when it
was handed out and whether a relay has passed it back since. Kept in the bridge's database, so a
bridge restart doesn't drop them: the voice call runs in the Claude app's cloud and outlives it.

This is the fork's own module, kept out of upstream's server.py so merges from upstream don't meet it.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from . import following

if TYPE_CHECKING:  # only for its type
    from .store import Store

# The settings row holding the handles: {handle: {"used": when, "sessions": {session id: mark}}}.
SETTING = "relay_handles"
# A handle the bridge hands out: "@" and four letters of mixed case, such as "@kQzw", so no one takes
# it for a name, nor for the owner's codes (P62, F70); l, I, o and O left out, as easily misread.
LETTERS = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ"
# A handle that follows nothing and wasn't used for this long is dropped.
IDLE_SECONDS = 24 * 3600
# Handles handed out this close together, none passed back yet, came from one relay's calls made at
# once without a handle: the first passed back takes the others over (owner, 2026-11-12). Two
# relays starting within this time would merge, which the owner says doesn't happen.
MERGE_SECONDS = 10
# A handle handed out to a call that forgot or mistyped the relay's own, not passed back since, is
# folded into the relay's handle when it next passes it, within this time (owner, 2026-11-13).
STRAY_SECONDS = 60


class Handles:
    """The relays' handles and their marks, as kept in the bridge's database."""

    def __init__(self, store: Store, clock: Callable[[], float]) -> None:
        self.store = store
        self.clock = clock
        self.all: dict[str, dict[str, Any]] = dict(store.get_setting(SETTING) or {})
        self.saved = self.snapshot()

    def marks(self, handle: str) -> dict[str, dict[str, Any]]:
        """A handle's marks, by session id; a handle not kept yet starts empty."""
        # RULE: each handle follows its own sessions
        return self.all.setdefault(handle, {"used": self.clock(), "sessions": {}})["sessions"]

    def follows(self, handle: str) -> list[str]:
        """The sessions a handle follows, by name."""
        return [
            str(m.get("name") or sid)
            for sid, m in self.all[handle]["sessions"].items()
            if following.stance(m) == "followed"
        ]

    def in_use(self) -> list[dict[str, Any]]:
        return [{"handle": h, "follows": self.follows(h)} for h in self.all]

    def find(self, given: str) -> str | None:
        """The handle kept that `given` names, whatever its capitals; None for one not kept, since
        a typo would otherwise quietly start an empty list."""
        # RULE: a handle not in use counts as none
        return next((h for h in self.all if h.casefold() == given.casefold()), None)

    def touch(self, handle: str) -> None:
        """Note that a relay used `handle` now."""
        self.all.setdefault(handle, {"used": 0, "sessions": {}})["used"] = self.clock()

    def drop_idle(self, keep: str | None = None) -> None:
        """Drop the handles following nothing and unused for a day, but `keep`, the caller's."""
        now = self.clock()
        for h in list(self.all):
            # RULE: a handle following nothing unused for a day is dropped
            if h != keep and not self.follows(h) and now - self.all[h].get("used", 0) > IDLE_SECONDS:
                del self.all[h]

    def snapshot(self) -> str:
        """The handles as kept, to tell whether they changed since the last save."""
        return json.dumps(self.all, sort_keys=True)

    def save(self) -> None:
        self.store.set_setting(SETTING, self.all)
        self.saved = self.snapshot()

    def save_if_changed(self) -> None:
        """Save the handles if anything changed since the last save, whatever changed them: a
        relay's last use and a dropped handle are kept over a restart too."""
        # RULE: a change to the handles is kept whatever made it
        if self.snapshot() != self.saved:
            self.save()

    def hand_out(self) -> str:
        """A new handle, kept from now on, that no relay has passed back yet."""
        while True:
            # RULE: a handle looks like a code, not a name
            handle = "@" + "".join(secrets.choice(LETTERS) for _ in range(4))
            mixed = handle != handle.lower() and handle != handle.upper()
            if mixed and not any(h.casefold() == handle.casefold() for h in self.all):
                break
        now = self.clock()
        self.all[handle] = {"used": now, "sessions": {}, "handed_out": now, "claimed": False}
        return handle

    def merge_batch(self, handle: str) -> list[str]:
        """Fold into `handle` the handles handed out with it that no one has passed back: calls a
        relay made at once without one, each given its own. Returns what they followed."""
        own, now = self.all[handle], self.clock()
        if own.get("handed_out") is not None:
            own["claimed"] = True
        merged = []
        for other in [h for h in self.all if h != handle]:
            theirs = self.all[other]
            if theirs.get("claimed") is not False:
                continue
            # RULE: handles handed out at the same moment merge on first use
            batch = own.get("handed_out") is not None and abs(theirs["handed_out"] - own["handed_out"]) <= MERGE_SECONDS
            # A relay that forgot or mistyped its handle wrote to a session it already had: a new
            # relay's first handle, on sessions this one never touched, is left alone, even while
            # another relay's waits call every half minute.
            followed = [sid for sid, m in theirs["sessions"].items() if following.stance(m) == "followed"]
            # RULE: a stray handle folds into the next handle passed that already had its sessions
            stray = now - theirs["handed_out"] <= STRAY_SECONDS and bool(followed)
            stray = stray and all(sid in own["sessions"] for sid in followed)
            if batch or stray:
                merged += self.follows(other)
                for sid, mark in theirs["sessions"].items():
                    if following.stance(own["sessions"].get(sid, {})) != "followed":
                        own["sessions"][sid] = mark
                del self.all[other]
        return merged
