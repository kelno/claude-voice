"""What the bridge says to agents, printed without serving it (a fork feature: UPSTREAM.md).

`claude-voice texts` prints, as JSON, the texts an agent reads from the bridge: its opening
instructions, each tool's name and the text an agent reads for it (its description as tools/list
serves it, then each parameter's description), the guide as the guide tool returns it, the notes
the bridge puts under a relayed message (with placeholders in angle brackets where it fills in a
value), the files config.toml names as notes for relays, and what the bridge hands over with the
guide besides it (`relay`: the note on the guide, the opening of the notes for relays, and the line
standing for one it can't read), each with when. They are read from a server built the way `serve`
builds it, with a throwaway store, so the printout and what agents read can't drift apart; nothing
is started, and the running bridge is left alone."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from . import config, relay_notes, roles
from .server import (
    GUIDE_NOTE,
    NAME_CHECK_FREE,
    NAME_CHECK_OWNERS,
    RELAYED_MESSAGE_NOTE,
    build_server,
    session_note,
)
from .sessions import SessionManager
from .store import Store

SENDER = "<sender>"
NAME = "<session name>"
# The texts the bridge hands over with the guide besides the guide itself (`relay`), each with when:
# the note on the guide, and what wraps the owner's notes for relays (relay_notes.py).
GUIDE_NOTE_WHEN = "with the guide, when the conversation's first call to the bridge isn't to guide"
OPENING_WHEN = "before the owner's notes for relays, handed to a relay with the guide"
UNREAD_WHEN = "in place of one of the owner's notes for relays that the bridge can't read"
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


def tool_text(tool: Any) -> str:
    """What an agent reads for a tool: its description as tools/list serves it, then a line
    `<parameter>: <description>` for each parameter that has one, in the schema's order. The same format
    the owner's path tools give their own server's tools; the key stays `description`, which they read."""
    params = (tool.input_schema or {}).get("properties", {})
    return (tool.description or "") + "".join(
        f"\n{name}: {p['description']}" for name, p in params.items() if isinstance(p, dict) and p.get("description")
    )


async def collect(repos: config.Config | None = None) -> dict[str, Any]:
    """The texts, from a bridge built with these settings (by default, the settings file's)."""
    repos = repos or config.load(config.FILE, own_clone=roles.own_clone())
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        where = Path(folder)
        manager = SessionManager(Store(where / "texts.db"), project_root=where)
        srv = build_server(manager, conversations=lambda **_: [], live_dir=where / "live", repo_config=repos)
        tools = [{"name": t.name, "description": tool_text(t)} for t in await srv.list_tools()]
        guide = getattr(await srv.call_tool("guide", {}), "structured_content", None) or {}
    return {
        "instructions": srv.instructions or "",
        "tools": tools,
        "guide": str(guide.get("guide", "")),
        "notes": [{"when": when, "text": text} for when, text in NOTES],
        "relay_notes": [str(p) for p in relay_notes.configured(repos.file)],
        "relay": {
            "guide_note": {"when": GUIDE_NOTE_WHEN, "text": GUIDE_NOTE},
            "opening": {"when": OPENING_WHEN, "text": relay_notes.OPENING},
            "unread": {"when": UNREAD_WHEN, "text": relay_notes.UNREAD.format(path="<path>", name="<name>")},
        },
    }
