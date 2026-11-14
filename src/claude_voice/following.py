"""Where a session stands for the relay: at rest or working, and, for one it follows, whether its
reply has come and been read.

Each relay keeps one mark per session it has read or follows (by handle: handles.py). A mark holds:
- `seen`: the last turn the relay has read;
- `sent`: while the relay follows the session, the turn after which it expects a finished turn (its
  message, its question, or a hand-off), with `probe`, the first words of its own message, and
  `replied`, whether that turn was seen to end; also `since` (when following started), `told`
  (whether a pause on a permission prompt was said), `state` and `last` (how it stood at the last
  call, and its latest message);
- `then` and `then_probe`: a message sent while an earlier reply was still unread, followed once that
  reply is read;
- `quiet`: once a followed reply is read, when the quiet follow started or last saw something new;
- `name` and `cwd`: to name the session and read its conversation once it has ended.

This is the fork's own module, kept out of upstream's server.py so merges from upstream don't meet it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

# A session whose turn has ended while a task it started runs on in the background: the task's
# completion note will start another turn, so it is not at rest (owner, 2026-11-12, ledger F58).
BACKGROUND = "turn ended, background task still running"
PERMISSION_PROMPT = "waiting on a permission prompt"
ASKING = "waiting for an answer"
# The states in which a session's turn has ended: at rest, or asking its user something.
TURN_ENDED = ("idle", ASKING)

Stance = Literal["followed", "quiet", "read"]
Where = Literal["unread", "read", "waiting", "working"]


def session_state(d: dict[str, Any] | None, background_running: Callable[[dict[str, Any]], bool]) -> str:
    """Where a running session stands, as the relay is told it: "ended" (not running), "idle" (at
    rest), BACKGROUND, PERMISSION_PROMPT, ASKING or "busy". `d` is Claude Code's listing of the
    session; `background_running` says whether a turn ended with a background task still running."""
    if d is None:
        return "ended"
    if d.get("status") == "waiting":
        return PERMISSION_PROMPT if d.get("waitingFor") == "permission prompt" else ASKING
    if d.get("status") in ("idle", "shell") and background_running(d):
        # RULE: a session with a background task still running is not at rest
        return BACKGROUND
    return "idle" if d.get("status") == "idle" else "busy"


def stance(mark: dict[str, Any]) -> Stance:
    """How the relay holds a session: "followed" (a finished turn is expected), "quiet" (its reply
    was read; only a new turn is news) or "read" (only a read mark)."""
    if mark.get("sent") is not None:
        return "followed"
    return "quiet" if mark.get("quiet") is not None else "read"


def own_message(turns: list[dict[str, Any]], mark: dict[str, Any]) -> int | None:
    """Where the relay's own message landed after the turn it was sent at, or None: not yet, or
    not as a turn (a message to a busy session can come in a form that isn't one)."""
    probe = mark.get("probe")
    if not probe:
        return None
    # RULE: a message the owner typed is never taken for the relay's own
    relayed = [i for i, t in enumerate(turns) if t.get("origin") in ("peer", "channel")]
    return next((i for i in relayed if i > mark["sent"] and probe in turns[i]["text"]), None)


def follow_state(turns: list[dict[str, Any]], mark: dict[str, Any], state: str) -> tuple[Where, str | None]:
    """Where a followed, running session stands, from its turns and its state: "unread" (its turn
    ended, not read yet), "read" (read: following it is over), "waiting" (paused on a permission
    prompt, mid-turn) or "working"; and its latest message since the relay's, if any. Notes in the
    mark once the reply is seen to have ended."""
    # Claude Code queues a message to a busy session until its turn ends: what comes before
    # the relay's own message is the end of that turn, not a reply to it.
    # RULE: a follow waits for its own message to land
    landed = own_message(turns, mark)
    start = mark["sent"] if landed is None else landed
    answer = [i for i, t in enumerate(turns) if t["role"] == "assistant" and i > start]
    # Its turn has ended once it rests, or asks its user something (not mid-turn on a
    # prompt), or once a later message came in after its reply.
    # RULE: a background task's completion note is not a later message
    ended = state in TURN_ENDED or any(
        t["role"] == "user" and t.get("origin") != "task-notification" and answer and i > answer[0]
        for i, t in enumerate(turns)
    )
    mark["replied"] = mark["replied"] or (bool(answer) and ended)
    last = turns[answer[-1]]["text"] if answer else None
    if mark["replied"]:
        return ("unread" if any(i > mark["seen"] for i in answer) else "read"), last
    if state == PERMISSION_PROMPT:
        return "waiting", last
    return "working", last


def unread_reply(turns: list[dict[str, Any]], mark: dict[str, Any]) -> bool:
    """Whether the session wrote anything after the relay's message that the relay hasn't read."""
    return any(t["role"] == "assistant" and i > max(mark["sent"], mark["seen"]) for i, t in enumerate(turns))


def quiet_new_turn(turns: list[dict[str, Any]], mark: dict[str, Any], state: str) -> dict[str, Any] | None:
    """The message that started a quietly followed session's new finished turn the relay hasn't
    read ({} when none is found), or None: nothing new, or a turn still running."""
    unread = [i for i, t in enumerate(turns) if t["role"] == "assistant" and i > mark["seen"]]
    if not unread or state not in TURN_ENDED:
        return None
    return next((t for t in reversed(turns[: unread[0]]) if t["role"] == "user"), {})
