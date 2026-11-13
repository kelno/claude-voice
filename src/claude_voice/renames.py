"""Renaming a running session: the name the bridge shows, the old names that still reach it,
and, where the session runs in tmux and is idle, `/rename` typed into Claude Code itself.

Claude Code keeps a session's name in its own record. The bridge cannot change that record,
only ask Claude Code to by typing `/rename`, and only where a terminal it can type into
exists. So the bridge keeps its own name on top: shown while Claude Code still has the
old one, dropped once Claude Code has the new one or someone renames it another way.
"""

from __future__ import annotations

import secrets
import subprocess
from collections.abc import Callable, Iterable
from typing import Any

LONGEST = 64
# Around a name an agent chose, so the owner sees it may change by itself; braces, which a voice
# leaves unsaid and nothing else in the bridge puts around a word (owner, 2026-11-13).
OPEN, CLOSE = "{", "}"
# Claude Code's nameSource for a title it made itself, which nobody chose.
AUTOMATIC = {"auto", "derived"}


def clean(name: str) -> str:
    """A session name on one line: control characters become spaces, runs of spaces one, at most
    LONGEST characters. The fork takes any visible text, as a start does: upstream's narrower names
    were for typing into tmux, which the fork never does."""
    return " ".join("".join(" " if not ch.isprintable() else ch for ch in name).split())[:LONGEST].strip()


def valid(name: str) -> bool:
    # RULE: a session name needs at least one visible character
    return bool(name.strip())


def is_marked(name: str) -> bool:
    """Whether a name is in the braces that mark a name an agent chose."""
    name = name.strip()
    return len(name) > 2 and name.startswith(OPEN) and name.endswith(CLOSE)


def bare(name: str) -> str:
    """A name without its braces: said aloud, a name comes back without them."""
    return name.strip()[1:-1].strip() if is_marked(name) else name.strip()


def marked(name: str) -> str:
    """A name an agent chose, in one pair of braces, within LONGEST characters."""
    return OPEN + bare(name)[: LONGEST - 2].strip() + CLOSE


def owners(d: dict[str, Any]) -> bool:
    """Whether the owner chose this running session's name: not marked, and not Claude Code's own
    automatic title (the bridge's name on top of it doesn't count as automatic)."""
    name = str(d.get("name") or "")
    if is_marked(name):
        return False
    # RULE: claude code's own title may be renamed by an agent
    return d.get("deliver_to") is not None or d.get("nameSource") not in AUTOMATIC


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
    by a name it had before; with `any_case`, whatever the capitals and the marker."""

    def same(a: Any) -> bool:
        if a is None:
            return False
        name, wanted = str(a), session
        if any_case:
            # RULE: a session is found by its name without the marker
            name, wanted = bare(name), bare(wanted)
        # RULE: a session name is found whatever its capitals
        return name.casefold() == wanted.casefold() if any_case else name == wanted

    if same(d.get("name")) or same(str(d.get("sessionId"))):
        return True
    # RULE: a message to an old name reaches the renamed session
    return old_names and (any(same(n) for n in d.get("aliases", [])) or same(d.get("deliver_to")))


class Ambiguous(LookupError):
    """A name that, its capitals aside, names more than one running session."""

    def __init__(self, session: str, names: list[str]) -> None:
        listed = ", ".join(repr(n) for n in names)
        super().__init__(f"{session!r} could be any of {listed}, which differ only by capitals")
        self.names = names


def find(rows: list[dict[str, Any]], session: str) -> dict[str, Any] | None:
    """The running session `session` names: its own name first, then a name it had; then the same
    whatever the capitals, as a name said aloud comes back with the capitals the transcription chose.
    Raises Ambiguous when, capitals aside, it names more than one session and none exactly."""
    for old_names in (False, True):
        for d in rows:
            if called(d, session, old_names):
                return d
    for old_names in (False, True):
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
    """Whether `name` is taken, its marker aside: names that differ only by it would be found as one."""
    # RULE: a name that differs only by the marker is in use
    return bare(name) in {bare(n) for n in taken(rows, but)}


def unique(wanted: str, taken_names: set[str]) -> str:
    """`wanted`, or with a short suffix until no name in `taken_names` has it, inside its braces if it has them."""
    mark, base = is_marked(wanted), bare(wanted)
    bares = {bare(n) for n in taken_names}
    name = base
    while name in bares:
        name = f"{base}-{secrets.token_hex(2)}"
    return marked(name) if mark else name


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
