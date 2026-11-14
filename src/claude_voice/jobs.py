"""Claude Code's background jobs, as the fork drives them: listing them, renaming one through its job
record, reading the permission prompt one waits on, telling which Claude Code closed for low memory,
and compacting a stopped one's conversation.

These are the fork's own; they live apart from upstream's transcripts.py so a merge from upstream
doesn't meet them there.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from . import procs
from .transcripts import clean_env

# Claude Code keeps one record per background session here, as <short id>/state.json.
JOBS_DIR = Path("~/.claude/jobs").expanduser()


_CSI = re.compile(r"\x1b\[[0-9;?]*([A-Za-z])")


_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


_DRAWING = str.maketrans({c: " " for c in "╌─│❯"})


_OPTION = re.compile(r"\d\. ")


def prompt_from_screen(screen: str) -> str | None:
    """The permission prompt at the end of a session's screen, as plain text, if it waits on one.

    Claude Code's screen shows the tool call it wants to run, what it would do, the
    question and the numbered answers; the drawing around them is dropped.
    """
    text = _CSI.sub(lambda m: " " if m[1] == "C" else "", _OSC.sub("", screen))
    lines = [" ".join(line.translate(_DRAWING).split()) for line in re.split(r"\r\n|\r|\n", text)]
    lines = [line for line in lines if line]
    start = max((i for i, line in enumerate(lines) if line.startswith("● ")), default=None)
    options = [i for i, line in enumerate(lines) if _OPTION.match(line)]
    if start is None or not options or options[-1] < start:
        return None
    block = lines[start : options[-1] + 1]
    block[0] = block[0][2:]
    return "\n".join(block)[:800]


def pending_prompt(job_id: str) -> str | None:
    """The permission prompt a background session waits on, read from its screen (`claude logs`)."""
    import shutil
    import subprocess

    try:
        out = subprocess.run(
            [shutil.which("claude") or "claude", "logs", job_id],
            capture_output=True,
            timeout=15,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return prompt_from_screen(out.stdout.decode("utf-8", errors="replace"))


def rename_job(session_id: str, name: str, jobs_dir: Path = JOBS_DIR) -> None:
    """Give a background session a new name through its job record, as Claude Code allows.

    A running background session watches its job record and renames itself at once when another
    process writes a new name there; waking it again starts it with the record's saved flags, so
    the name in `--name` is changed too. The record is replaced whole, never half-written.
    """
    for path in jobs_dir.glob("*/state.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if record.get("sessionId") != session_id:
            continue
        record["name"], record["nameSource"] = name, "user"
        flags = record.get("respawnFlags")
        if isinstance(flags, list) and "--name" in flags[:-1]:
            flags[flags.index("--name") + 1] = name
        fresh = path.with_name("state.json.rename")
        fresh.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(fresh, path)
        return
    raise FileNotFoundError(f"no job record for {session_id}")


# Claude Code's background manager writes here when it starts and closes background sessions.
DAEMON_LOG = Path.home() / ".claude" / "daemon.log"


_RETIRE = re.compile(r"\[bg\] bg retire (\S+?):.*\[low memory\]")


_SPAWN = re.compile(r"\[bg\] bg spawned (\S+)")


def closed_for_memory(log: Path = DAEMON_LOG) -> set[str]:
    """The short ids (a session id's first 8 characters) of the background sessions Claude Code
    closed to free memory and that haven't run since. Their conversations are kept."""
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return set()
    closed: set[str] = set()
    for line in lines:
        if m := _RETIRE.search(line):
            closed.add(m.group(1))
        elif m := _SPAWN.search(line):
            closed.discard(m.group(1))
    return closed


async def background_jobs() -> list[dict[str, Any]]:
    """Every background session Claude Code knows, finished ones too, as `claude agents` lists them:
    sessionId, cwd, name, and state (running, done, stopped, failed, ...)."""
    import claude_agent_sdk

    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    cli = procs.find("claude") or str(bundled)
    listing = await asyncio.create_subprocess_exec(
        cli, "agents", "--json", "--all", stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE
    )
    raw, _ = await asyncio.wait_for(listing.communicate(), 60)
    try:
        jobs = json.loads(raw.decode(errors="replace") or "[]")
    except ValueError:
        return []
    return [j for j in jobs if isinstance(j, dict) and j.get("sessionId")] if isinstance(jobs, list) else []


async def background_sessions() -> set[str]:
    """The ids of every conversation Claude Code has run as a background session, finished ones too."""
    return {str(j["sessionId"]) for j in await background_jobs()}


COMPACT_SECONDS = 600  # how long a compaction may take before it counts as failed


def compact_command(cli: str, session_id: str, instructions: str | None = None) -> list[str]:
    """The command that compacts a stopped session's conversation in place, without a terminal:
    Claude Code runs /compact in print mode and writes the summary into the same transcript."""
    return [cli, "-p", f"/compact {instructions or ''}".strip(), "--resume", session_id]


async def compact_stopped(cwd: str, session_id: str, instructions: str | None = None) -> None:
    """Compact a stopped session's conversation, as typing /compact in it would.

    The session must not be running: two processes writing one conversation would clash. Its
    PreCompact hooks run as for a /compact typed in it."""

    import claude_agent_sdk

    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    cli = procs.find("claude") or str(bundled)
    proc = await asyncio.create_subprocess_exec(
        *compact_command(cli, session_id, instructions),
        cwd=cwd,
        env=clean_env(os.environ),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), COMPACT_SECONDS)
    except TimeoutError:
        proc.kill()
        raise RuntimeError(f"the compaction took longer than {COMPACT_SECONDS} seconds") from None
    if proc.returncode:
        raise RuntimeError(
            out.decode(errors="replace").strip()[-300:] or f"claude -p /compact exited {proc.returncode}"
        )
