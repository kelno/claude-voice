"""The owner's notes for relays, handed over with the guide (a fork feature: UPSTREAM.md).

The guide is for every agent on the bridge; how a relay works with the owner and the other sessions
in the owner's own setup lives in the owner's notes, outside the repository, and changes often while
that setup is tried out. So config.toml names those files (a top-level `relay_notes`, a list of
paths), and the bridge reads them each time it hands the guide to a relay, so an edit reaches the
next relay without a restart. Only relays get them: callers that aren't Claude Code sessions."""

from __future__ import annotations

import logging
import re
import tomllib
from pathlib import Path

log = logging.getLogger("claude_voice")

# A note longer than this is cut: the relay reads the notes with every guide it is handed.
NOTE_BYTES = 32 * 1024
# What the relay reads first: what these notes are, since the guide doesn't name them.
OPENING = (
    "The owner's notes for you as the relay, from their own setup: read them with the guide, "
    "which they build on. A link [[path|text]] names another note; those given here are below."
)
UNREAD = (
    "The bridge couldn't read this note ({path}): work from the guide and the notes below, "
    "and tell the owner once that the bridge couldn't read their note {name}."
)
FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def configured(file: Path | None) -> list[Path]:
    """The notes config.toml names; none without the file or the key. A file that can't be
    parsed is the rest of the config's problem: here it means no notes."""
    if file is None or not file.is_file():
        return []
    try:
        raw = tomllib.loads(file.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return []
    named = raw.get("relay_notes")
    if not isinstance(named, list):
        return []
    return [Path(str(p)).expanduser() for p in named if p]


def text(paths: list[Path]) -> str | None:
    """The notes as one text, each under its name; None when none is configured. A note that
    can't be read is named in its place, so the relay works on and tells the owner."""
    if not paths:
        return None
    parts = [OPENING]
    for path in paths:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            log.warning("relay note %s could not be read (%s); relays are told so.", path, exc.strerror or exc)
            parts.append(f"# {path.stem}\n{UNREAD.format(path=path, name=path.stem)}")
            continue
        if len(raw) > NOTE_BYTES:
            log.warning("relay note %s is %d bytes, so relays get the first %d.", path, len(raw), NOTE_BYTES)
            raw = raw[:NOTE_BYTES]
        body = raw.decode("utf-8", errors="ignore").replace("\r\n", "\n").replace("\r", "\n")
        parts.append(f"# {path.stem}\n{FRONTMATTER.sub('', body).strip()}")
    return "\n\n".join(parts) + "\n"
