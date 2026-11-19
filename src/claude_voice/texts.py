"""What the bridge says to agents, printed without serving it (a fork feature: UPSTREAM.md).

`claude-voice texts` prints, as JSON, the texts an agent reads from the bridge: its opening
instructions, each tool's name and description as tools/list serves them, the guide as the guide
tool returns it, the notes the bridge puts under a relayed message (with placeholders in angle
brackets where it fills in a value), and the files config.toml names as notes for relays. They are
read from a server built the way `serve` builds it, with a throwaway store, so the printout and
what agents read can't drift apart; nothing is started, and the running bridge is left alone."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from . import config, relay_notes, roles
from .server import NAME_CHECK_FREE, NAME_CHECK_OWNERS, RELAYED_MESSAGE_NOTE, build_server, session_note
from .sessions import SessionManager
from .store import Store

SENDER = "<sender>"
NAME = "<session name>"
# When the bridge adds each note, in the order a session meets them.
NOTES = (
    ("under a message the relay sends into a session (it has no inbox)", RELAYED_MESSAGE_NOTE),
    (
        f"under a message the Claude Code session {SENDER} sends with message_active_session",
        session_note(SENDER, waits=False),
    ),
    (
        f"under a question the Claude Code session {SENDER} asks with ask_active_session",
        session_note(SENDER, waits=True),
    ),
    (
        "after that note, at most once an hour per session, when an agent chose the session's name",
        NAME_CHECK_FREE.format(name=NAME),
    ),
    (
        "after that note, at most once an hour per session, when the owner chose the session's name",
        NAME_CHECK_OWNERS.format(name=NAME),
    ),
)


async def collect(repos: config.Config | None = None) -> dict[str, Any]:
    """The texts, from a bridge built with these settings (by default, the settings file's)."""
    repos = repos or config.load(config.FILE, own_clone=roles.own_clone())
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        where = Path(folder)
        manager = SessionManager(Store(where / "texts.db"), project_root=where)
        srv = build_server(manager, conversations=lambda **_: [], live_dir=where / "live", repo_config=repos)
        tools = [{"name": t.name, "description": t.description or ""} for t in await srv.list_tools()]
        guide = getattr(await srv.call_tool("guide", {}), "structured_content", None) or {}
    return {
        "instructions": srv.instructions or "",
        "tools": tools,
        "guide": str(guide.get("guide", "")),
        "notes": [{"when": when, "text": text} for when, text in NOTES],
        "relay_notes": [str(p) for p in relay_notes.configured(repos.file)],
    }
