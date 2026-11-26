"""Renaming a running session: the name the bridge shows, the old names that still reach it,
and, where the session runs in tmux and is idle, `/rename` typed into Claude Code itself.

Claude Code keeps a session's name in its own record. The bridge cannot change that record,
only ask Claude Code to by typing `/rename`, and only where a terminal it can type into
exists. So the bridge keeps its own name on top: shown while Claude Code still has the
old one, dropped once Claude Code has the new one or someone renames it another way.
"""

from __future__ import annotations

import re
import secrets
import subprocess
from collections.abc import Callable, Iterable
from typing import Any

LONGEST = 64
# Anywhere in a name, marks it as the owner's, in the name Claude Code and the app show, so the
# owner sees which names are protected (owner, 2026-11-26). Claude Code's own record can't tell:
# it records a title given at launch as chosen by someone, as it does a name the owner gave.
MARK = "*"


def clean(name: str) -> str:
    """A session name on one line: control characters become spaces, runs of spaces one, at most
    LONGEST characters. The fork takes any visible text, as a start does: upstream's narrower names
    were for typing into tmux, which the fork never does."""
    return " ".join("".join(" " if not ch.isprintable() else ch for ch in name).split())[:LONGEST].strip()


def valid(name: str) -> bool:
    # RULE: a session name needs at least one visible character
    return bool(name.strip())


def is_marked(name: str) -> bool:
    """Whether a name holds the asterisk that marks it as the owner's."""
    # RULE: an asterisk anywhere makes a name the owner's
    return MARK in name


def bare(name: str) -> str:
    """A name without its asterisks: said aloud, a name comes back without them."""
    return " ".join(name.replace(MARK, "").split())


def owners(d: dict[str, Any]) -> bool:
    """Whether this running session's name is the owner's: whether it holds the asterisk."""
    return is_marked(str(d.get("name") or ""))


def refused(old: str, new: str) -> str | None:
    """Why an agent, without the owner's say (owner_chose), may not change a session's name from
    `old` to `new`, at a rename, a start (`old` empty) or a resume: "asterisk" when it would give the
    name an asterisk, "owners" when `old` is the owner's; None when it may."""
    if new == old:
        return None
    # RULE: only the owner gives a session name an asterisk
    if is_marked(new) and not is_marked(old):
        return "asterisk"
    # RULE: an agent cannot rename a session the owner named
    if is_marked(old) and not (is_marked(new) and only_status(old, new)):
        return "owners"
    return None


# A status in round brackets at the end of a name, such as " (waiting)".
STATUS = re.compile(r"\s+\([^()]*\)$")


def only_status(old: str, new: str) -> bool:
    """Whether `new` is `old` with a status in round brackets added at its end, or `old` with one
    dropped: the one change an agent may make to a name the owner chose, so a session can show its
    state on it (owner, 2026-11-17). Replacing part of the name, brackets included, isn't one."""

    def drop(name: str) -> str | None:
        found = STATUS.search(name)
        return name[: found.start()] if found else None

    # RULE: an agent may add and drop a status after a name the owner chose
    return drop(new) == old or drop(old) == new


def apply(rows: Iterable[dict[str, Any]], names: dict[str, tuple[str, str]], aliases: dict[str, list[str]]):
    """The running sessions with the bridge's names on top. `names` maps a session id to
    (the name Claude Code had when it was renamed, the new name); `aliases` its old names.
    `deliver_to` is the name Claude Code itself still answers to, when it differs."""
    out = []
    for d in rows:
        sid = str(d.get("sessionId"))
        d = {**d, "aliases": aliases.get(sid, [])}
        was, new = names.get(sid, (None, None))
        if new and d.get("name") == was and was != new:
            d["deliver_to"], d["name"] = d.get("name"), new
        out.append(d)
    return out


def called(d: dict[str, Any], session: str, old_names: bool = False, any_case: bool = False) -> bool:
    """Whether `session` names this running session: by name or id, or with `old_names`
    by a name it had before; with `any_case`, whatever the capitals and the asterisk."""

    def same(a: Any) -> bool:
        if a is None:
            return False
        name, wanted = str(a), session
        if any_case:
            # RULE: a session is found by its name without the asterisk
            name, wanted = bare(name), bare(wanted)
        # RULE: a session name is found whatever its capitals
        return name.casefold() == wanted.casefold() if any_case else name == wanted

    if same(d.get("name")) or same(str(d.get("sessionId"))):
        return True
    # RULE: a message to an old name reaches the renamed session
    return old_names and (any(same(n) for n in d.get("aliases", [])) or same(d.get("deliver_to")))


class Ambiguous(LookupError):
    """A name that, its capitals and asterisk aside, names more than one running session."""

    def __init__(self, session: str, names: list[str]) -> None:
        listed = ", ".join(repr(n) for n in names)
        super().__init__(f"{session!r} could be any of {listed}, which differ only by capitals or an asterisk")
        self.names = names


def find(rows: list[dict[str, Any]], session: str) -> dict[str, Any] | None:
    """The running session `session` names: by its own name, exactly, then whatever the capitals and
    the asterisk, as a name said aloud comes back with the capitals the transcription chose and no
    asterisk; only then by a name it had, the same way, so another session's old name never beats a
    session's own ("Coordinator" finds "Coordinator*" before the session once called "Coordinator").
    Raises Ambiguous when, capitals and asterisk aside, it names more than one session and none exactly."""
    for old_names in (False, True):
        # RULE: a session's own name is found before another's old name
        exact = next((d for d in rows if called(d, session, old_names)), None)
        if exact is not None:
            return exact
        found = [d for d in rows if called(d, session, old_names, any_case=True)]
        # RULE: names that differ only by capitals are all refused unless one is exact
        if len(found) > 1:
            raise Ambiguous(session, [str(d.get("name")) for d in found])
        if found:
            return found[0]
    return None


def answers_to(d: dict[str, Any]) -> list[str]:
    """Every name a running session answers to: its own, a name it had, and Claude Code's."""
    return [str(n) for n in (*d.get("aliases", []), d.get("deliver_to"), d.get("name")) if n]


def by_name(rows: Iterable[dict[str, Any]]) -> dict[str, str]:
    """Each name a running session answers to, and that session's id."""
    return {name: str(d.get("sessionId")) for d in rows for name in answers_to(d)}


def taken(rows: Iterable[dict[str, Any]], but: str | None = None) -> set[str]:
    """Every name a running session answers to, other than session `but`'s."""
    return {name for name, sid in by_name(rows).items() if sid != but}


def in_use(name: str, rows: Iterable[dict[str, Any]], but: str | None = None) -> bool:
    """Whether `name` is taken, its asterisk aside: names that differ only by it would be found as one."""
    # RULE: a name that differs only by the asterisk is in use
    return bare(name) in {bare(n) for n in taken(rows, but)}


def unique(wanted: str, taken_names: set[str]) -> str:
    """`wanted`, or with a short suffix until no name in `taken_names` has it, its asterisk aside."""
    bares = {bare(n) for n in taken_names}
    name = wanted
    while bare(name) in bares:
        name = f"{wanted}-{secrets.token_hex(2)}"
    return name


class Tmux:
    """Typing into a tmux pane, found by the process running in it."""

    def __init__(self, run: Callable[[list[str]], str] | None = None) -> None:
        self.run = run or self._run

    @staticmethod
    def _run(command: list[str]) -> str:
        return subprocess.run(command, check=True, capture_output=True, text=True, timeout=5).stdout

    def pane(self, pid: int) -> tuple[str, str] | None:
        """(tmux session, pane) where process `pid` runs, if it runs directly in a pane."""
        try:
            listing = self.run(["tmux", "list-panes", "-a", "-F", "#{pane_pid} #{session_name} #{pane_id}"])
        except (OSError, subprocess.SubprocessError):
            return None
        for line in listing.splitlines():
            parts = line.split(" ")
            if len(parts) == 3 and parts[0] == str(pid):
                return parts[1], parts[2]
        return None

    def type(self, pane: str, text: str) -> None:
        self.run(["tmux", "send-keys", "-t", pane, "-l", text])
        self.run(["tmux", "send-keys", "-t", pane, "Enter"])

    def rename(self, old: str, new: str) -> None:
        self.run(["tmux", "rename-session", "-t", f"={old}", new])
