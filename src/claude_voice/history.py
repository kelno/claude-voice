"""Earlier conversations for a page listing every session (a fork feature: UPSTREAM.md).

The owner controls their sessions from a page beside the chat, since the desktop app doesn't let
them: running ones and stopped ones, newest first, a search by name as they type, and older ones
a page at a time. list_claude_conversations gives the stopped ones; this module picks which of
them a call returns and says what kind of session each was, so the page can mark the ones the
bridge runs (no window) apart from the desktop app's and a terminal's.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

from claude_agent_sdk import SDKSessionInfo, project_key_for_directory

from . import every_session

log = logging.getLogger("claude_voice")

# How far into a transcript to look for where the session ran: its first entries say.
HEAD_LINES = 40


def title(c: SDKSessionInfo) -> str:
    """The name a conversation is known by: the one it was given, else Claude Code's summary."""
    return c.custom_title or c.summary or ""


def matches(c: SDKSessionInfo, query: str | None) -> bool:
    """Whether a conversation's name holds the words searched for, whatever their case."""
    return not query or query.casefold().strip() in title(c).casefold()


def background_ids(jobs_dir: Path) -> set[str]:
    """The sessions Claude Code keeps a job record for: those it ran with no window."""
    return {k["claude_session_id"] for k in every_session.job_records(jobs_dir)}


def ran_in(session_id: str, cwd: str, projects_dir: Path) -> str | None:
    """Where a conversation ran, from the first entrypoint its transcript names: "desktop" or
    "terminal"; None when the transcript is missing or names none (a program's own session)."""
    path = projects_dir / project_key_for_directory(cwd) / f"{session_id}.jsonl"
    try:
        with path.open(encoding="utf-8") as f:
            for _, line in zip(range(HEAD_LINES), f, strict=False):
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                where = entry.get("entrypoint") if isinstance(entry, dict) else None
                if where == "claude-desktop":
                    return "desktop"
                if where == "cli":
                    return "terminal"
                if where:
                    return None
    except (OSError, UnicodeDecodeError) as exc:
        log.debug("no transcript to tell where %s ran (%s)", session_id, exc)
    return None


def kinder(jobs_dir: Path, projects_dir: Path) -> Callable[[SDKSessionInfo], str | None]:
    """What kind each conversation was: background (no window), desktop, terminal, or None when
    nothing says. The job records are read once per call, not once per conversation."""
    background = background_ids(jobs_dir)

    def kind(c: SDKSessionInfo) -> str | None:
        if c.session_id in background:
            return "background"
        return ran_in(c.session_id, c.cwd or "", projects_dir)

    return kind
