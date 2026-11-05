"""The MCP face of the bridge: tools a voice assistant can call, over stdio or HTTP."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import logging.handlers
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import SDKSessionInfo, project_key_for_directory
from claude_agent_sdk import list_sessions as list_claude_sessions
from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl
from starlette.types import ASGIApp

from . import access, events, fork, metrics, permissions, transcripts
from .metrics import METRICS
from .oauth import ACCESS_TTL, MIN_ACCESS_TTL, SCOPE, OAuthProvider, PublicClientMetadata
from .sessions import SessionManager
from .store import Store
from .tree import PROJECTS_DIR, SessionTree, session_kind

MIN_TOKEN_CHARS = 32
READ_ONLY = ToolAnnotations(read_only_hint=True)
COURIER_PREFIX = "claude-voice-msg-"
# Added after every message the relay sends into a running session: the session can't know
# otherwise that nobody reads its screen and that a SendMessage reply goes nowhere.
RELAYED_MESSAGE_NOTE = (
    "[From the claude-voice bridge: this message comes from the relay, the voice assistant the "
    "owner is talking to. The owner sees neither this message nor your reply unless the relay "
    "passes them on, and the relay has no inbox: answer in your reply text, not with SendMessage. "
    "Address the relay, not the owner, and make the reply self-contained: name things instead of "
    "pointing back to text it may not have.]"
)
# Claude Code starts a session on any model name and only refuses an unknown one in its first reply.
MODEL_CHECKED_LATER = (
    "Claude Code only checks the model name at the session's first reply: wait for that reply "
    "before relying on the session."
)
# The Claude desktop app names a side chat, opened from a session for a quick aside, "btw: <question>".
SIDE_CHAT_PREFIX = "btw:"
# The Claude app drops a tool call that runs for about 30 seconds: no call waits longer than
# this, and past it the call says the work is still going and how to read on.
MAX_WAIT_SECONDS = 20.0
# How much text a read of a session's turns returns when no count is asked for: many short
# progress turns fit, a few long reports don't flood the reply.
READ_BUDGET = 3_000
# Reading forward, a turn that doesn't fit the budget still shows at least this much of its
# start, so an unread reply can't be mistaken for no reply.
READ_START = 300
# How long a side session may work in the background, and how long its answer is kept.
FORK_LIFETIME_SECONDS = 300.0
FORK_KEPT_SECONDS = 3600.0
# Claude Code keeps one JSON file per running session here.
LIVE_DIR = Path("~/.claude/sessions").expanduser()
# The log is kept in a file next to the database too: the console is gone after a restart,
# and a connector dropped overnight is diagnosed from what the token endpoint saw.
LOG_FILE = "bridge.log"
LOG_BYTES = 5_000_000
LOG_KEPT = 3

# The manual and the tool descriptions are designed together. The Claude app shows an agent
# each tool by the first line of its description, cut at about eighty characters, so each
# tool's first line says what it is for, and the manual holds only what spans tools: roles and
# the central rules. Agents are assumed to read the manual; one that doesn't is a bug to fix
# here (the guide tool's first line, a clearer rule), not a case to design around, so the tool
# descriptions never repeat the manual's rules as reminders.
INSTRUCTIONS = """\
Read this first, before any other call: how the claude-voice bridge is used.

Roles
- Voice relay: the owner talks to you, a voice assistant, and sees no screen.
  You are the relay. The Claude Code session doing the work at the owner's
  machine is the desk session. A read-only fork of it, asked one question while
  it works, is the side session (the forked_session tools).
- The mode is per message: the same desk session may talk with the owner
  directly when they are at the machine.

The central rule: the owner sees nothing that passes through these tools
- Not what you send, not what a session answers, not what agents say to each
  other. Everything that matters must be said in your conversation with the
  owner: say briefly what you sent and what came back.
- Keep agents distinct: "the desk session says X", "the side session says X",
  "I think Y". Never present another agent's words as your own, or blend them.
- Sessions you write to are told this too: they answer you, the relay, in their
  reply text, written for an agent and self-contained; you choose what to say
  aloud and condense it. The owner is usually speaking, often away from the
  machine, so keep what you read back short.

The relay's scope
- Relaying to a session that owns a piece of work, pass on the owner's words
  and the context it lacks. How to do the work (its structure, wording, plan)
  is that session's call. An idea of your own goes along only as a suggestion
  marked as yours, and a change within that session's plan goes to it rather
  than being done by you on the side.

Waiting
- No call waits longer than 20 seconds, whatever wait_seconds asks: the app
  drops a call after about 30. When the owner asks you to wait for a session's
  answer, in whatever words, keep waiting: call wait_for_session again while it
  says "still_working". Otherwise don't block the conversation: hand back and
  check on their next turn. Before any wait, say a two- or three-word
  heads-up, such as "Waiting on it.": silence during a wait sounds like a
  dropped call.
- A session's reply is final only when its turn has ended (reply_final true,
  or status "idle" with turns_left 0). Before that it is its latest message so
  far: don't report it as finished, or as cut off.

Sessions
- New work: start_active_session, then give it its task. Never ask a desktop
  session to start one: the app refuses a start from a relayed message and
  puts a card in front of the owner. Sessions the bridge starts or resumes are
  on Remote Control and in the desktop app's sidebar, so the owner can open
  them on the phone or at the PC and take over.
- A session's permission prompt is the owner's to answer, on the phone or in
  the desktop app, never yours. When a session's status is "waiting", prompt
  says what it asks: tell the owner.

Missing tools
- The app caches this connector's tool list. If a tool named here isn't in
  yours, search for it by its exact name; if it still isn't there, tell the
  owner the tool list needs a refresh in claude.ai Settings → Connectors.
"""


class ConfigError(RuntimeError):
    pass


@dataclass
class Config:
    root: Path
    db: Path
    transport: str
    host: str = "127.0.0.1"
    port: int = 8811
    token: str | None = None
    public_hosts: list[str] = field(default_factory=list)
    public_url: str = ""
    courier_name: str = transcripts.COURIER_NAME
    new_session_mode: str = "default"
    access_ttl: int = ACCESS_TTL


MAKE_TOKEN = (
    "Make one with `export CLAUDE_VOICE_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')`."
)


def load_config(env: Mapping[str, str], transport: str) -> Config:
    # RULE: an api key in the environment stops the bridge from starting
    if env.get("ANTHROPIC_API_KEY") and env.get("CLAUDE_VOICE_ALLOW_API_KEY") != "1":
        raise ConfigError(
            "ANTHROPIC_API_KEY is set, so Claude would bill the API instead of your "
            "subscription. Unset it, or set CLAUDE_VOICE_ALLOW_API_KEY=1 if that is intended."
        )
    root = Path(env.get("CLAUDE_VOICE_ROOT", "~/src")).expanduser()
    if not root.is_dir():
        raise ConfigError(
            f"CLAUDE_VOICE_ROOT is not a directory: {root}. Make it with `mkdir -p {root}`, or set "
            "CLAUDE_VOICE_ROOT to the folder that holds your projects."
        )
    cfg = Config(
        root=root,
        db=Path(env.get("CLAUDE_VOICE_DB", "~/.local/state/claude-voice/bridge.db")).expanduser(),
        transport=transport,
        host=env.get("CLAUDE_VOICE_HOST", "127.0.0.1"),
        port=int(env.get("CLAUDE_VOICE_PORT", "8811")),
        token=env.get("CLAUDE_VOICE_TOKEN"),
        public_hosts=[h for h in env.get("CLAUDE_VOICE_PUBLIC_HOSTS", "").split(",") if h],
    )
    cfg.courier_name = env.get("CLAUDE_VOICE_COURIER_NAME", "").strip() or transcripts.COURIER_NAME
    cfg.new_session_mode = env.get("CLAUDE_VOICE_PERMISSION_MODE", "").strip() or "default"
    if cfg.new_session_mode not in permissions.MODES:
        raise ConfigError(
            f"CLAUDE_VOICE_PERMISSION_MODE is {cfg.new_session_mode!r}; "
            f"use one of {', '.join(sorted(permissions.MODES))}."
        )
    ttl = env.get("CLAUDE_VOICE_ACCESS_TOKEN_SECONDS", "").strip()
    if ttl:
        # RULE: an access-token lifetime that is not a whole number of at least a minute stops the bridge
        if not ttl.isdigit() or int(ttl) < MIN_ACCESS_TTL:
            raise ConfigError(
                f"CLAUDE_VOICE_ACCESS_TOKEN_SECONDS is {ttl!r}; use a whole number of seconds, "
                f"at least {MIN_ACCESS_TTL} (an hour is 3600, a week 604800)."
            )
        cfg.access_ttl = int(ttl)
    cfg.public_url = env.get("CLAUDE_VOICE_PUBLIC_URL", "").rstrip("/") or (
        f"https://{cfg.public_hosts[0]}" if cfg.public_hosts else f"http://127.0.0.1:{cfg.port}"
    )
    if transport == "http":
        if not cfg.token:
            raise ConfigError(f"CLAUDE_VOICE_TOKEN must be set to serve over HTTP. {MAKE_TOKEN}")
        if len(cfg.token) < MIN_TOKEN_CHARS:
            raise ConfigError(f"CLAUDE_VOICE_TOKEN must be at least {MIN_TOKEN_CHARS} characters. {MAKE_TOKEN}")
    return cfg


log = logging.getLogger("claude_voice")


def _parse_cursor(cursor: str | None) -> int | None:
    """The feed position a cursor names; None, so the caller starts over, for any other text."""
    try:
        return int(cursor or "")
    except ValueError:
        return None


async def log_requests(ctx, call_next):
    """Log and count each MCP call: method, tool, outcome, time and the caller's trace id.

    A voice client sends a trace id per spoken turn as params._meta.trace_id; it is
    kept for the courier too, so one search follows a turn through every part.
    """
    params = ctx.params if isinstance(ctx.params, dict) else {}
    tool = str(params.get("name", ""))
    what = f"{ctx.method} {tool}".rstrip()
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    trace = str(meta.get("trace_id") or "") or None
    token = transcripts.current_trace.set(trace)
    started = time.monotonic()
    try:
        result = await call_next(ctx)
    except Exception as exc:
        ms = (time.monotonic() - started) * 1000
        log.info("mcp %s -> %s %dms trace=%s", what, type(exc).__name__, ms, trace or "-")
        if ctx.method == "tools/call":
            METRICS.record(tool, ms, error=f"{type(exc).__name__}: {exc}", kind="exception", trace_id=trace)
        raise
    except asyncio.CancelledError:
        # The client gave up on the call (the Claude app drops one after about 30 s).
        ms = (time.monotonic() - started) * 1000
        log.info("mcp %s -> cancelled by the client %dms trace=%s", what, ms, trace or "-")
        raise
    finally:
        transcripts.current_trace.reset(token)
    ms = (time.monotonic() - started) * 1000
    if ctx.method == "tools/call":
        failed = getattr(result, "is_error", None) or (isinstance(result, dict) and result.get("isError"))
        log.info("mcp %s -> %s %dms trace=%s", what, "error" if failed else "ok", ms, trace or "-")
        error = None
        if failed:
            content = getattr(result, "content", None) or (result.get("content") if isinstance(result, dict) else None)
            first = content[0] if content else None
            error = str(getattr(first, "text", None) or (first.get("text") if isinstance(first, dict) else first) or "")
        METRICS.record(tool, ms, error=error, trace_id=trace)
    elif ctx.request_id is not None:
        log.info("mcp %s", what)
    return result


def build_server(
    manager: SessionManager,
    oauth: OAuthProvider | None = None,
    conversations: Callable[..., list[SDKSessionInfo]] = list_claude_sessions,
    live_dir: Path = LIVE_DIR,
    deliver: Callable[[str, str], Awaitable[dict[str, Any]]] = transcripts.deliver,
    read_transcript: Callable[[str, str | None], list[Any]] | None = None,
    watch_interval: float | None = None,
    restart: dict[str, Any] | None = None,
    start_background: Callable[..., Awaitable[str | None]] = transcripts.start_background,
    background_sessions: Callable[[], Awaitable[set[str]]] = transcripts.background_sessions,
    stop_background: Callable[[str], Awaitable[None]] = transcripts.stop_background,
    open_in_desktop: Callable[[str, str, str], Awaitable[None]] = transcripts.open_in_desktop,
    start_seconds: float = 20.0,
    summarize: Callable[[str, str | None, str | None], Awaitable[str]] = transcripts.summarize,
    digest_chars: int = 150_000,
    side_answer: Callable[[str, str, str | None], Awaitable[str]] | None = None,
    side_chars: int = 8_000,
    poll_seconds: float = 1.0,
    receive_seconds: float = 15.0,
    projects_dir: Path = PROJECTS_DIR,
    max_wait_seconds: float = MAX_WAIT_SECONDS,
    read_prompt: Callable[[str], str | None] = transcripts.pending_prompt,
) -> MCPServer:
    def capped(seconds: float) -> float:
        """How long a call may wait: what it asked for, never past the cap."""
        # RULE: no tool call waits longer than the cap
        return max(0.0, min(seconds, max_wait_seconds))

    auth = None
    if oauth is not None:
        auth = AuthSettings(
            issuer_url=AnyHttpUrl(oauth.public_url),
            resource_server_url=AnyHttpUrl(f"{oauth.public_url}/mcp"),
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]
            ),
            required_scopes=[SCOPE],
            revocation_options=RevocationOptions(enabled=True),
            validate_token_resource=False,
        )
    if read_transcript is None:

        def read_transcript(sid: str, directory: str | None) -> list[Any]:
            return transcripts.read_raw_transcript(sid, directory or "", projects_dir)

    # under_root is defined further down; looked up when called, so granted folders count.
    watcher = events.LiveWatcher(
        manager.store, live_dir, manager.root, clock=manager.clock, project_of=lambda cwd: under_root(cwd)
    )
    tree = SessionTree(
        live_dir,
        manager.root,
        projects_dir=projects_dir,
        clock=manager.clock,
        project_of=lambda cwd: under_root(cwd),
    )
    side_answer = side_answer or transcripts.shared_reader()
    tree_version: list[str] = []  # the last shape seen, so a change can be reported
    last_check: list[str] = []
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    restart = restart or {"at": started_at, "reason": None, "total": None}
    running_version = metrics.version()

    def check() -> None:
        """Look at the running sessions and the tree's shape; record what changed."""
        watcher.check()
        version = tree.build()["version"]
        if tree_version and tree_version[0] != version:
            watcher.record("claude-voice", "tree_changed", version)
        tree_version[:] = [version]
        last_check[:] = [datetime.now(UTC).isoformat(timespec="seconds")]

    @contextlib.asynccontextmanager
    async def lifespan(_server):
        # Watch running sessions between polls, so short-lived states are not missed.
        task = None
        if watch_interval:

            async def watch():
                while True:
                    with contextlib.suppress(Exception):
                        check()
                    await asyncio.sleep(watch_interval)

            task = asyncio.create_task(watch())
            # The service keeps the side-question reader ready, so the first answer is quick too.
            warming = asyncio.create_task(transcripts.shared_reader().warm())
            warming.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        try:
            yield {}
        finally:
            if task:
                task.cancel()

    mcp = MCPServer(
        "claude-voice",
        title="Claude voice bridge",
        version=running_version,
        instructions=INSTRUCTIONS,
        auth_server_provider=oauth,
        auth=auth,
        middleware=[log_requests],
        lifespan=lifespan,
    )
    if oauth is not None:
        mcp.custom_route("/oauth/consent", methods=["GET"])(oauth.consent_page)
        mcp.custom_route("/oauth/consent", methods=["POST"])(oauth.consent_submit)
    store = manager.store

    def history_folder(conversations_dir: Path, by_key: dict[str, Path]) -> Path | None:
        """The real folder of one of Claude Code's history folders, which are named by a lossy
        key: a folder under the root by its key, otherwise the working folder a conversation records."""
        if conversations_dir.name in by_key:
            return by_key[conversations_dir.name]
        for f in sorted(conversations_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)[:3]:
            try:
                with f.open(encoding="utf-8") as lines:
                    for _, line in zip(range(20), lines, strict=False):
                        cwd = json.loads(line).get("cwd")
                        if cwd:
                            return Path(cwd)
            except (OSError, ValueError, AttributeError):
                continue
        return None

    @mcp.tool(title="Read the bridge's manual", annotations=READ_ONLY)
    def guide() -> dict[str, Any]:
        """Read this first, before any other call: the rules every agent here follows.
        The same text as the server's instructions; read it again when unsure how a role or a
        rule works."""
        return {"guide": INSTRUCTIONS}

    @mcp.tool(title="List recent project folders", annotations=READ_ONLY)
    def list_projects(query: str | None = None) -> dict[str, Any]:
        """Folders to start a session in: used by Claude Code lately, or found by a word.
        The folders Claude Code was used in during the last 30 days, anywhere on this machine,
        newest first, from its own history; a word (`query`) also finds folders under the root
        never used. Each has access like list_active_sessions (absent when open; name is relative
        to the root when under it, else the full path), at most 20."""
        children = [p for p in manager.root.iterdir() if p.is_dir() and not p.name.startswith(".")]
        by_key = {project_key_for_directory(str(p)): p for p in children}
        found: dict[Path, tuple[float, int]] = {p.resolve(): (0.0, 0) for p in children}
        for d in projects_dir.iterdir() if projects_dir.is_dir() else []:
            used = [f.stat().st_mtime for f in d.glob("*.jsonl")] if d.is_dir() else []
            path = history_folder(d, by_key) if used else None
            if path is None or Path(path).name.startswith(COURIER_PREFIX):
                continue
            key = Path(path).resolve()
            newest, count = found.get(key, (0.0, 0))
            found[key] = (max(newest, *used), count + len(used))
        rows = []
        for path, (newest, count) in found.items():
            if not path.is_dir():
                continue  # deleted since; its history outlives it
            if query and query.lower() not in path.name.lower():
                continue
            if not query and newest < time.time() - 30 * 24 * 3600:
                continue
            name = under_root(str(path))
            row: dict[str, Any] = {
                "name": name if name is not None else str(path),
                "last_used": datetime.fromtimestamp(newest, UTC).isoformat(timespec="minutes") if newest else None,
                "sessions": count,
            }
            if name is None:
                row["access"] = f"folder_{manager.access.state(path)}"
            rows.append((newest, row))
        rows.sort(key=lambda r: (-r[0], r[1]["name"]))
        return {"root": str(manager.root), "projects": [row for _, row in rows[:20]]}

    @mcp.tool(title="Show what every session is doing", annotations=READ_ONLY)
    def fleet_recap() -> dict[str, Any]:
        """Every running session with its status and one line on what it is doing.
        Answers "what are my sessions up to?" without reading each one; kind is "desktop" (open
        in the desktop app), "terminal" or "background", and side_chat marks a quick aside the
        owner opened from another session in the desktop app (named "btw: ..."), which gets no
        work unless asked."""
        sessions = []
        live_by_id = {str(d.get("sessionId")): d for d in live_sessions()}
        for row in running():
            if "access" in row:
                # Not open to the bridge: nothing is read from it.
                item = {k: v for k, v in row.items() if k != "claude_session_id"}
                sessions.append({"id": row["claude_session_id"], **item, "doing": ""})
                continue
            d = live_by_id.get(str(row["claude_session_id"]), {})
            if row["status"] == "moved":
                where = row["moved_to"]["name"] or "another session"
                doing = f"moved to {where}"
            else:
                doing = transcripts.doing_line(str(row["claude_session_id"]), str(d.get("cwd")), projects_dir)
            item = {
                "id": row["claude_session_id"],
                "name": row["name"],
                "kind": session_kind({**d, "kind": row["kind"]}),
                "project": row["project"],
                "status": row["status"],
                "doing": doing,
                "minutes_since_update": row["minutes_since_update"],
            }
            for key in ("waiting_for", "question", "moved_to", "side_chat"):
                if key in row:
                    item[key] = row[key]
            sessions.append(item)
        return {"sessions": sessions}

    @mcp.tool(
        title="Start a background session", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False)
    )
    async def start_active_session(
        project: str, name: str | None = None, model: str | None = None, effort: str | None = None
    ) -> dict[str, Any]:
        """Start new work: a background session in a project, on the phone and at the PC.
        A new Claude Code session in the background in a project (from list_projects), named
        `name` or after the project, on Remote Control and in the desktop app's Code sidebar from
        the start, so the owner can open it anywhere, see what happened and take over; give it
        its task with message_active_session or ask_active_session. Optional: `model`, any name
        Claude Code accepts (for example opus, sonnet, claude-opus-5-5, ...; not a complete list;
        a wrong one shows only in the first reply), and `effort`, one of low, medium, high,
        xhigh, max; left out, Claude Code's defaults apply; for deep work (restructuring,
        careful reviews) pick a bigger model and a higher effort, and say which when proposing
        it."""
        # RULE: only a well-formed model name and a known effort reach Claude Code
        if model is not None and not transcripts.MODEL_NAME.fullmatch(model):
            raise ToolError(f"Not a model name: {model!r}. Use an alias such as opus, or a full model name.")
        if effort is not None and effort not in transcripts.EFFORTS:
            raise ToolError(f"Unknown effort {effort!r}; use one of: {', '.join(transcripts.EFFORTS)}.")
        try:
            path = manager.resolve_project(project)
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        out = await launch(path, name, model=model, effort=effort)
        return {**out, **({"note": MODEL_CHECKED_LATER} if model else {})}

    async def launch(path: Path, name: str | None, **options: Any) -> dict[str, Any]:
        """Start a background session in `path` under a free name, and wait until it is listed.

        A woken session keeps the name it was saved with, so it is found by its id instead.
        """
        wake = options.get("wake")
        taken = {str(d.get("name")) for d in live_sessions()}
        wanted = "" if wake else (name or "").strip()[:60] or f"{path.name}-{secrets.token_hex(2)}"
        while wanted in taken:
            wanted = f"{wanted}-{secrets.token_hex(2)}"
        try:
            printed = await start_background(str(path), wanted, **options) or ""
        except Exception as exc:
            raise ToolError(f"Claude Code could not start a session: {exc}") from None
        notes = [line.strip() for line in printed.splitlines() if line.strip().startswith("note:")]

        def listed(d: dict[str, Any]) -> bool:
            if d.get("cwd") != str(path):
                return False
            return d.get("sessionId") == options.get("resume") if wake else d.get("name") == wanted

        deadline = time.monotonic() + start_seconds
        while time.monotonic() < deadline:
            found = next((d for d in live_sessions() if listed(d)), None)
            if found:
                started_as = str(found.get("name") or wanted)
                store.record_started(str(found.get("sessionId")), started_as, str(path))
                return {
                    "name": started_as,
                    "claude_session_id": found.get("sessionId"),
                    "project": under_root(str(path)),
                    "status": found.get("status") or "idle",
                    **({"claude_code_note": " ".join(notes)} if notes else {}),
                }
            await asyncio.sleep(0.2)
        raise ToolError(f"{wanted or name} was started but did not appear among the running sessions")

    @mcp.tool(
        title="Stop a background session", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True)
    )
    async def stop_active_session(name: str) -> dict[str, Any]:
        """Stop a background session the bridge started; any other session is refused.
        One start_active_session started; another, such as one open in a terminal, gets status
        not_started_here."""
        target = next((d for d in live_sessions() if name in (d.get("name"), d.get("sessionId"))), None)
        if target is None:
            return {"name": name, "status": "not_running"}
        sid = str(target.get("sessionId"))
        # RULE: only a session the bridge started itself is ever stopped
        if not store.was_started(sid):
            return {"name": name, "status": "not_started_here"}
        try:
            await stop_background(sid)
        except Exception as exc:
            raise ToolError(f"Claude Code could not stop {name}: {exc}") from None
        deadline = time.monotonic() + start_seconds
        while any(d.get("sessionId") == sid for d in live_sessions()):
            if time.monotonic() > deadline:
                raise ToolError(f"{name} was asked to stop but is still running")
            await asyncio.sleep(0.2)
        return {"name": name, "status": "stopped"}

    @mcp.tool(
        title="Move a session into the desktop app",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
    )
    async def hand_to_desktop(name: str) -> dict[str, Any]:
        """Only when the owner asks: move a started session into the desktop sidebar.
        A session start_active_session started, once its turn has ended, goes into its project's
        group in the desktop app's sidebar with its conversation: the bridge stops it and opens a
        terminal tab on the PC, named after it, that hands it over; there it waits for the owner
        to open it, and until then it is off the phone. status: "handed_over", "busy" (mid-turn;
        nothing done), "no_conversation" (it never answered, so there is nothing to open), or
        "not_started_here"."""
        live = next((d for d in live_sessions() if name in (d.get("name"), d.get("sessionId"))), None)
        started = store.started_session(str(live.get("sessionId")) if live else name)
        if started is None:
            return {"name": name, "status": "not_started_here"}
        sid, cwd = started["claude_session_id"], started["cwd"]
        name = started["name"]
        if live and live.get("status") != "idle":
            return {"name": name, "claude_session_id": sid, "status": "busy"}
        # Claude Code writes no conversation before the first turn: nothing to open then.
        if not any(t["role"] == "assistant" for t in transcripts.recent_turns(read_transcript(sid, cwd), None)):
            return {"name": name, "claude_session_id": sid, "status": "no_conversation"}
        if live:
            # The desktop app only takes a session that is no longer running elsewhere.
            await stop_active_session(sid)
        try:
            await open_in_desktop(sid, cwd, name)
        except Exception as exc:
            raise ToolError(f"Could not open {name} in the desktop app: {exc}") from None
        return {"name": name, "claude_session_id": sid, "status": "handed_over", "terminal_tab": name}

    def under_root(cwd: str | None) -> str | None:
        """Project name: relative to the root, the full path in a granted folder, or None
        if the folder is not open to the bridge."""
        if not cwd:
            return None
        try:
            path = manager.resolve_project(cwd)
        except ValueError:
            return None
        if manager.access.base(path) == manager.root:
            return str(path.relative_to(manager.root))
        return str(path)

    def is_courier(d: dict[str, Any]) -> bool:
        """The bridge's own messengers, gone as soon as they deliver."""
        return str(d.get("name", "")).startswith(COURIER_PREFIX) or Path(str(d.get("cwd"))).name.startswith(
            COURIER_PREFIX
        )

    def live_sessions() -> list[dict[str, Any]]:
        return events.read_live(live_dir)

    @mcp.tool(title="List the sessions running now", annotations=READ_ONLY)
    def list_active_sessions() -> dict[str, Any]:
        """Every session running now, with status; a waiting one carries its prompt.
        Claude Code sessions on this machine, busy, idle or waiting; kind "interactive" is one
        the user types in, "bg" a background one; side_chat marks a desktop side chat ("btw:
        ..."), which gets no work unless asked. A session outside the open folders has access
        "folder_not_open" (open it with request_access, which the owner approves in their app) or
        "folder_closed" (never opened; a refusal names the closed folder), and only its name,
        folder and status; access is absent when the session is open to the bridge, and a
        background session waiting on a permission prompt carries it as prompt, which the owner
        answers on the phone or in the desktop app."""
        return {"sessions": running()}

    def moved_to(d: dict[str, Any]) -> dict[str, Any] | None:
        """Where a running session's conversation went, if Claude Code continued it elsewhere."""
        new = transcripts.continued_in(str(d.get("sessionId")), str(d.get("cwd")), projects_dir)
        if not new:
            return None
        name = next((x.get("name") for x in live_sessions() if x.get("sessionId") == new), None)
        return {"id": new, "name": name}

    def permission_prompt(d: dict[str, Any]) -> str | None:
        """The permission prompt a waiting background session shows its owner, if any."""
        if d.get("waitingFor") != "permission prompt" or not d.get("jobId"):
            return None
        return read_prompt(str(d["jobId"]))

    def running() -> list[dict[str, Any]]:
        now_ms = manager.clock() * 1000
        sessions = []
        for d in live_sessions():
            if is_courier(d):
                continue
            name = under_root(d.get("cwd"))
            if name is None:
                # Not open to the bridge: who and where only, so the owner can open it.
                sessions.append(
                    {
                        "claude_session_id": d.get("sessionId"),
                        "name": d.get("name"),
                        "folder": str(Path(str(d.get("cwd"))).resolve()),
                        "status": d.get("status"),
                        "kind": d.get("kind"),
                        "access": f"folder_{manager.access.state(str(d.get('cwd')))}",
                        "minutes_since_update": round((now_ms - d.get("updatedAt", now_ms)) / 60000),
                    }
                )
                continue
            row = {
                "claude_session_id": d.get("sessionId"),
                "name": d.get("name"),
                "project": name,
                "status": d.get("status"),
                "kind": d.get("kind"),
                "minutes_since_update": round((now_ms - d.get("updatedAt", now_ms)) / 60000),
            }
            if str(d.get("name") or "").startswith(SIDE_CHAT_PREFIX):
                row["side_chat"] = True
            moved = moved_to(d)
            if moved:
                row["status"], row["moved_to"] = "moved", moved
            elif d.get("status") == "waiting":
                row["waiting_for"] = d.get("waitingFor")
                question = transcripts.open_question(str(d.get("sessionId")), str(d.get("cwd")), projects_dir)
                if question:
                    row["question"] = question
                prompt = permission_prompt(d)
                if prompt:
                    row["prompt"] = prompt
            sessions.append(row)
        sessions.sort(key=lambda s: s["minutes_since_update"])
        return sessions

    @mcp.tool(title="List recent conversations", annotations=READ_ONLY)
    def list_claude_conversations(project: str | None = None, limit: int = 10) -> dict[str, Any]:
        """Earlier conversations, newest first; resume one with attach_conversation.
        Recent Claude Code conversations on this machine, including ones started in a terminal;
        optionally only for one project."""
        directory = None
        if project:
            try:
                directory = str(manager.resolve_project(project))
            except ValueError as exc:
                raise ToolError(str(exc)) from None
        limit = min(max(limit, 1), 50)
        now_ms = manager.clock() * 1000
        open_ids = {d.get("sessionId") for d in live_sessions()}
        listed = []
        for c in conversations(directory=directory, limit=limit * 5):
            name = under_root(c.cwd)
            if name is None:
                continue
            listed.append(
                {
                    "claude_session_id": c.session_id,
                    "project": name,
                    "summary": c.custom_title or c.summary,
                    "first_prompt": c.first_prompt,
                    "git_branch": c.git_branch,
                    "minutes_ago": round((now_ms - c.last_modified) / 60000),
                    "open_in_terminal": c.session_id in open_ids,
                }
            )
            if len(listed) == limit:
                break
        return {"conversations": listed}

    @mcp.tool(
        title="Continue an earlier conversation",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
    )
    async def attach_conversation(
        claude_session_id: str, project: str | None = None, label: str | None = None
    ) -> dict[str, Any]:
        """Resume an earlier conversation as a background session; then talk to it.
        Continues an existing Claude Code conversation (from list_claude_conversations) like
        start_active_session: on Remote Control under `label` or its title, in its own permission
        mode and folders, so the owner can open it on the phone or at the PC. If Claude Code
        can't continue it in place, as when it is still open elsewhere, it starts a copy under a
        new id, and copied_from names the original: say so; a claude_code_note is Claude Code's
        own remark on the start."""
        if project is None:
            live = next((d for d in live_sessions() if d.get("sessionId") == claude_session_id), None)
            match = None
            if live is None:
                match = next(
                    (c for c in conversations(limit=1000) if c.session_id == claude_session_id),
                    None,
                )
                if match is None:
                    raise ToolError(f"No Claude Code conversation with id {claude_session_id}")
            project = (live or {}).get("cwd") or (match.cwd if match else "") or ""
            label = label or (live or {}).get("name") or (match.custom_title or match.summary if match else None)
        try:
            path = manager.resolve_project(project)
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        if not transcripts.SESSION_ID.fullmatch(claude_session_id):
            raise ToolError(f"Not a Claude Code session id: {claude_session_id!r}")
        if claude_session_id in await background_sessions():
            # It keeps its own saved name, Remote Control, settings and mode; any flag would copy it.
            out = await launch(path, label, resume=claude_session_id, wake=True)
        else:
            grant = manager.grant_for(claude_session_id, str(path), manager.new_mode)
            out = await launch(path, label, resume=claude_session_id, permission_mode=grant.mode, add_dirs=grant.dirs)
        if out["claude_session_id"] != claude_session_id:
            out["copied_from"] = claude_session_id
        return out

    def find_any_live(session: str) -> dict[str, Any]:
        for d in live_sessions():
            if not is_courier(d) and session in (d.get("name"), d.get("sessionId")):
                return d
        raise ToolError(f"No running session called {session!r}; see list_active_sessions")

    def find_live(session: str) -> dict[str, Any]:
        d = find_any_live(session)
        if under_root(d.get("cwd")) is None:
            cwd = str(d.get("cwd"))
            if manager.access.state(cwd) == "closed":
                raise ToolError(f"{session!r}: {manager.access.explain(cwd)}")
            raise ToolError(
                f"{session!r} is in {Path(cwd).resolve()}, a folder not open to the bridge;"
                " open it with request_access (the owner approves it in their app)"
            )
        return d

    @mcp.tool(
        title="Ask the owner to open a folder", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True)
    )
    def request_access(session: str) -> dict[str, Any]:
        """Open a session's folder to the bridge for good; the owner approves in the app.
        A listed session's folder, and everything under it, so it can be read, messaged and
        worked in. The owner should set this tool to ask each time in the Claude app, so they
        approve every grant themselves."""
        d = find_any_live(session)
        try:
            folder = manager.access.grant(str(d.get("cwd")))
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        return {"session": d.get("name"), "folder": str(folder), "granted": True}

    @mcp.tool(
        title="Close an opened folder again", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False)
    )
    def revoke_access(folder: str) -> dict[str, Any]:
        """Close a folder opened with request_access; health lists the granted ones."""
        try:
            closed = manager.access.revoke(folder)
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        return {"folder": str(closed), "granted": False}

    @mcp.tool(
        title="Send a message to an open session",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
    )
    async def message_active_session(session: str, message: str) -> dict[str, Any]:
        """Send to a session and return at once; wait_for_session then gets the reply.
        A message into a running Claude Code session (name or id from list_active_sessions),
        returned once delivered, a few seconds; use it when your next step doesn't depend on the
        answer, then wait_for_session(after=next_after), or read_session_output(after=next_after)
        later."""
        target = find_live(session)
        moved = moved_to(target)
        if moved:
            return {"session": target["name"], "delivered": False, "status": "moved", "moved_to": moved}
        before = len(numbered_turns(target))
        out = await deliver(target["name"], f"{message}\n\n{RELAYED_MESSAGE_NOTE}")
        if not out.get("delivered"):
            raise ToolError(f"Could not deliver: {out.get('detail') or 'unknown reason'}")
        return {"session": target["name"], "status": "delivered", "delivered": True, "next_after": before - 1}

    def numbered_turns(target: dict[str, Any]) -> list[dict[str, Any]]:
        msgs = read_transcript(target["sessionId"], target.get("cwd"))
        return [{"index": i, **turn} for i, turn in enumerate(transcripts.recent_turns(msgs, None))]

    def waiting(d: dict[str, Any]) -> dict[str, Any] | None:
        """Why a session can take nothing in right now: an open question, or other input."""
        if d.get("status") != "waiting":
            return None
        question = transcripts.open_question(str(d.get("sessionId")), str(d.get("cwd")), projects_dir)
        if question:
            return {"status": "needs_choice", "question": question}
        prompt = permission_prompt(d)
        return {"status": "needs_input", **({"prompt": prompt} if prompt else {})}

    @mcp.tool(title="Wait for a session's turn to end", annotations=READ_ONLY)
    async def wait_for_session(
        session: str, wait_seconds: float = 20, after: int | None = None, max_chars: int = 600
    ) -> dict[str, Any]:
        """Wait up to 20 s until a session's turn ends or needs input; then its reply.
        Sends nothing. With `after` (the next_after a send returned) it waits for a reply past
        that turn, so a session that hasn't picked the message up yet is still waited for; without
        it an idle session returns at once. status: "idle" (its turn has ended; reply is its last
        words, turns what it said since `after`), "needs_input" or "needs_choice" (it waits for its
        user; prompt or question says what for), "still_working" (the wait ran out; call again),
        "session_ended" or "moved" (the conversation now lives in moved_to)."""
        target = find_live(session)
        moved = moved_to(target)
        if moved:
            return {"session": target["name"], "status": "moved", "moved_to": moved, "session_ended": False}

        def said(d: dict[str, Any]) -> list[dict[str, Any]]:
            """What the session has written, past `after` when one is given."""
            return [t for t in numbered_turns(d) if t["role"] == "assistant" and (after is None or t["index"] > after)]

        pid = target.get("pid")
        deadline = asyncio.get_running_loop().time() + capped(wait_seconds)
        status = "still_working"
        blocked = None
        while True:
            now = next((d for d in live_sessions() if d.get("pid") == pid), None)
            if now is None:
                status = "session_ended"
                break
            target = now
            blocked = waiting(now)
            if blocked:
                status = blocked["status"]
                break
            if now.get("status") == "idle" and (after is None or said(now)):
                status = "idle"
                break
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(poll_seconds)
        written = [] if status == "session_ended" else said(target)
        new = written if after is not None else written[-1:]
        for turn in new:
            turn["text"] = transcripts.clip(turn["text"], max(max_chars, 20))
        return {
            "session": target["name"],
            "status": status,
            "session_ended": status == "session_ended",
            "reply": new[-1]["text"] if new else "",
            "reply_final": bool(new) and status != "still_working",  # same rule as ask_active_session
            "turns": new,
            "next_after": new[-1]["index"] if new else (after if after is not None else -1),
            **({k: v for k, v in blocked.items() if k != "status"} if blocked else {}),
        }

    @mcp.tool(
        title="Ask an open session and wait", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False)
    )
    async def ask_active_session(
        session: str, message: str, wait_seconds: float = 20, max_chars: int = 600
    ) -> dict[str, Any]:
        """Ask a session and wait (20 s at most) for its answer; only when needed now.
        The conversation waits too, so prefer message_active_session when the next step doesn't
        depend on the answer. reply_final is true once its turn has ended, so reply is its last
        word; while false, reply is its latest message so far, whole and maybe already the
        answer, but more may follow. status: "answered", "needs_input" (it waits for its user;
        prompt is the permission prompt, if one), "still_working" (the wait ran out before its
        turn ended; continue with wait_for_session(after=next_after)), "session_ended" (it exited
        meanwhile), "not_received" (the message never reached its conversation), "moved" (the
        conversation now lives in moved_to; nothing sent), or "needs_choice" (it waits for its
        user to pick one of question.options; answer at the screen) - "needs_input" before
        sending means nothing was sent."""
        target = find_live(session)
        moved = moved_to(target)
        if moved:
            return {
                "session": target["name"],
                "status": "moved",
                "moved_to": moved,
                "session_ended": False,
                "reply": "",
                "turns": [],
                "next_after": -1,
            }

        blocked = waiting(target)
        if blocked:
            # A message would only queue behind what it waits for; send nothing.
            return {
                "session": target["name"],
                **blocked,
                "session_ended": False,
                "reply": "",
                "turns": [],
                "next_after": -1,
            }
        before = len(numbered_turns(target))
        was_busy = target.get("status") in ("busy", "shell")
        out = await deliver(target["name"], f"{message}\n\n{RELAYED_MESSAGE_NOTE}")
        if not out.get("delivered"):
            raise ToolError(f"Could not deliver: {out.get('detail') or 'unknown reason'}")

        pid, status, seen_busy = target.get("pid"), "still_working", False
        started = asyncio.get_running_loop().time()
        deadline = started + capped(wait_seconds)
        probe = message.strip()[:80]
        while True:
            now = next((d for d in live_sessions() if d.get("pid") == pid), None)
            if now is None:
                status = "session_ended"
                break
            target = now
            # A quick turn can start and end between two looks, so a new reply
            # counts as much as having seen the session busy.
            seen_busy = seen_busy or now.get("status") in ("busy", "shell")
            new_turns = numbered_turns(now)[before:]
            anchor_at = next((t["index"] for t in new_turns if t["role"] == "user" and probe in t["text"]), None)
            if (
                was_busy
                and anchor_at is not None
                and any(t["role"] == "assistant" and t["index"] > anchor_at for t in new_turns)
            ):
                # Asked mid-work: its first words after our question are the answer; the
                # rest of its turn belongs to whatever it was doing.
                status = "answered"
                break
            replied = seen_busy or any(t["role"] == "assistant" for t in new_turns)
            arrived = replied or any(t["role"] == "user" and probe in t["text"] for t in new_turns)
            if not arrived and asyncio.get_running_loop().time() - started >= receive_seconds:
                status = "not_received"  # handed over, yet never reached the conversation
                break
            if replied and now.get("status") == "idle":
                status = "answered"
                break
            if now.get("status") == "waiting" and (seen_busy or replied or arrived):
                blocked = waiting(now)
                status = blocked["status"] if blocked else "needs_input"
                break
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(poll_seconds)

        fresh = numbered_turns(target)[before:] if status != "session_ended" else []
        # Only what answers our message counts: what the session wrote after it. Its
        # last words are the reply; the rest of its turn is how it got there.
        anchor = next((t for t in fresh if t["role"] == "user" and probe in t["text"]), None)
        start = anchor["index"] if anchor else before - 1
        new = [t for t in fresh if t["role"] == "assistant" and t["index"] > start]
        if anchor is None and status != "still_working":
            new = new[-1:]  # the message is not in view: only the session's final words
        elif anchor is not None and was_busy and new:
            new = new[:1]
        for turn in new:
            turn["text"] = transcripts.clip(turn["text"], max(max_chars, 20))
        return {
            "session": target["name"],
            "status": status,
            "session_ended": status == "session_ended",
            "reply": new[-1]["text"] if new else "",
            # RULE: a reply is final only once the turn has ended
            "reply_final": bool(new) and status in ("answered", "needs_input", "needs_choice"),
            "turns": new,
            "next_after": new[-1]["index"] if new else start,
            **({"question": blocked["question"]} if status == "needs_choice" and blocked else {}),
        }

    @mcp.tool(title="Read an open session's latest turns", annotations=READ_ONLY)
    def read_session_output(
        session: str,
        limit: int | None = None,
        max_chars: int = 600,
        after: int | None = None,
        turn: int | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        """A session's latest turns, or those after `after`, until turns_left is 0.
        The latest turns of a running Claude Code session (name or id from list_active_sessions),
        as many as fit in about 3,000 characters unless `limit` sets a count. Turns are
        numbered: pass next_after back as `after` for the next ones, oldest first, with
        turns_left saying how many more follow (read on until it is 0, and take a reply as final
        only once the session's status is "idle"); the first turn that doesn't fit is still
        started. A turn longer than max_chars, or than the room left, is cut and says so (cut,
        length), and `turn` (its index) with `offset` reads it whole, page by page via
        next_offset."""
        target = find_live(session)
        turns = numbered_turns(target)
        size = max(max_chars, 20)
        if turn is not None:
            whole = next((t for t in turns if t["index"] == turn), None)
            if whole is None:
                raise ToolError(f"No turn {turn} in {target['name']}")
            start = min(max(offset, 0), len(whole["text"]))
            chunk = whole["text"][start : start + size]
            end = start + len(chunk)
            return {
                "session": target["name"],
                "status": target.get("status"),
                "turn": turn,
                "role": whole["role"],
                "text": chunk,
                "offset": start,
                "next_offset": end,
                "has_more": end < len(whole["text"]),
                "length": len(whole["text"]),
            }

        def taken(candidates: list[dict[str, Any]], forward: bool) -> list[tuple[dict[str, Any], int]]:
            """The turns a read takes from `candidates`, in order, each with the size it is cut to:
            `limit` of them, or what fits the budget. Reading forward, the first turn that doesn't
            fit is still started, cut to the room left, so what follows is never hidden entirely."""
            if limit is not None:
                return [(t, size) for t in candidates[: min(max(limit, 1), 30)]]
            picked: list[tuple[dict[str, Any], int]] = []
            used = 0
            for t in candidates[:30]:
                need = min(len(t["text"]), size)
                if picked and used + need > READ_BUDGET:
                    if forward:
                        picked.append((t, max(READ_BUDGET - used, READ_START)))
                    break
                picked.append((t, size))
                used += need
            return picked

        left: dict[str, int] = {}
        if after is None:
            picked = taken(turns[::-1], forward=False)[::-1]
        else:
            newer = [t for t in turns if t["index"] > after]
            picked = taken(newer, forward=True)
            left = {"turns_left": len(newer) - len(picked)}
        turns = []
        for t, room in picked:
            t["length"] = len(t["text"])
            t["cut"] = t["length"] > room
            t["text"] = transcripts.clip(t["text"], room)
            turns.append(t)
        next_after = turns[-1]["index"] if turns else (after if after is not None else -1)
        return {
            "session": target["name"],
            "status": target.get("status"),
            "turns": turns,
            "next_after": next_after,
            **left,
        }

    def conversation_end(target: dict[str, Any], limit: int) -> tuple[list[str], list[str]]:
        """A session's conversation as lines, the end kept up to `limit` characters, and the rest."""
        lines = []
        for t in numbered_turns(target):
            who = "Owner" if t["role"] == "user" else "Claude"
            tools = f" [used: {', '.join(t['tools'])}]" if t.get("tools") else ""
            lines.append(f"{who}: {t['text']}{tools}")
        kept: list[str] = []
        size = 0
        for line in reversed(lines):  # keep the end: that is where the session stands now
            if size + len(line) > limit and kept:
                break
            kept.append(line)
            size += len(line)
        kept.reverse()
        return kept, lines[: len(lines) - len(kept)]

    @mcp.tool(title="Take a quick look at a session", annotations=READ_ONLY)
    async def peek_session(session: str, question: str, language: str | None = None) -> dict[str, Any]:
        """A small model answers about a session's last turns in 3-7 s; it reads no code.
        A quick look at a running session: a small separate model with no tools answers
        `question` from the last 8,000 characters of its conversation, a few turns of words and
        tool names, never tool results or files. The question never enters the session; for the
        whole conversation use digest_session, which is slower."""
        target = find_live(session)
        kept, skipped = conversation_end(target, side_chars)
        answer = await side_answer("\n".join(kept), question, language)
        return {"answer": transcripts.whole_sentences(answer, 300), "considered_turns": len(kept), "cut": bool(skipped)}

    # Side sessions at work or done, by fork id; kept in memory only, so a restart forgets them.
    forks: dict[str, dict[str, Any]] = {}

    def start_fork(session: str, question: str) -> str:
        target = find_live(session)
        now = time.monotonic()
        for old in [k for k, f in forks.items() if f["task"].done() and now - f["started"] > FORK_KEPT_SECONDS]:
            del forks[old]

        async def run() -> dict[str, Any]:
            try:
                return await fork.ask(
                    manager.client_factory,
                    str(target["sessionId"]),
                    str(target["cwd"]),
                    question,
                    FORK_LIFETIME_SECONDS,
                )
            except Exception as exc:
                return {"status": "error", "answer": "", "tools_used": {}, "detail": f"{type(exc).__name__}: {exc}"}

        fork_id = secrets.token_hex(4)
        forks[fork_id] = {"task": asyncio.create_task(run()), "session": target.get("name"), "started": now}
        return fork_id

    async def collect_fork(fork_id: str, wait_seconds: float) -> dict[str, Any]:
        entry = forks.get(fork_id)
        if entry is None:
            raise ToolError(
                f"No side session {fork_id!r}: answers are kept for an hour, and forgotten when the bridge restarts"
            )
        task = entry["task"]
        await asyncio.wait({task}, timeout=capped(wait_seconds))
        head = {"fork_id": fork_id, "session": entry["session"]}
        if not task.done():
            return {**head, "status": "still_working", "answer": ""}
        return {**head, **task.result()}

    @mcp.tool(title="Ask the side session and wait", annotations=READ_ONLY)
    async def ask_forked_session(session: str, question: str, wait_seconds: float = 20) -> dict[str, Any]:
        """Ask the side session, a read-only fork that reads code; waits 20 s at most.
        The side session is a new read-only fork of a running session's whole conversation that
        can read the project's files, while the session itself keeps working, untouched; each
        question forks anew, from the latest state. status "answered" (report it as the side
        session's), "still_working" (read it later with read_forked_session(fork_id)),
        "timed_out", or "error" with `detail`."""
        return await collect_fork(start_fork(session, question), wait_seconds)

    @mcp.tool(title="Send the side session a question", annotations=READ_ONLY)
    async def message_forked_session(session: str, question: str) -> dict[str, Any]:
        """Send the side session a question and return; read it with read_forked_session.
        Like message_active_session: a new read-only fork of the session's conversation starts
        working on the question at once, and read_forked_session(fork_id) gets its answer
        later."""
        fork_id = start_fork(session, question)
        return {"fork_id": fork_id, "session": forks[fork_id]["session"], "status": "started"}

    @mcp.tool(title="Read the side session's answer", annotations=READ_ONLY)
    async def read_forked_session(fork_id: str, wait_seconds: float = 20) -> dict[str, Any]:
        """The side session's answer, waiting 20 s at most; call again on still_working.
        status "answered", "still_working" (call again), "timed_out" (it worked five minutes and
        was stopped) or "error" with `detail`; answers are kept for an hour, and forgotten when
        the bridge restarts."""
        return await collect_fork(fork_id, wait_seconds)

    @mcp.tool(title="Sum up a session's conversation", annotations=READ_ONLY)
    async def digest_session(session: str, question: str | None = None, language: str | None = None) -> dict[str, Any]:
        """Sum up a session's whole conversation, or answer a question on it, in ~10 s.
        A few speakable sentences, in `language` if given (for example "norsk"): a small separate
        model with no tools reads up to the last 150,000 characters of a running session's
        conversation, words and tool names, never tool results or files. The same kind of
        reader as peek_session with a far longer window, so slower; none of it fills your
        context, and it cannot read code."""
        target = find_live(session)
        kept, skipped = conversation_end(target, digest_chars)
        digest = await summarize("\n".join(kept), question, language)
        return {
            "session": target["name"],
            "digest": transcripts.whole_sentences(digest, 600),
            "turns_considered": len(kept),
            "cut": bool(skipped),
            "skipped_turns": len(skipped),
            "skipped_chars": sum(len(x) for x in skipped),
        }

    @mcp.tool(title="Search a session's conversation", annotations=READ_ONLY)
    def search_session_history(session: str, query: str, max_chars: int = 300) -> dict[str, Any]:
        """Search a session's whole conversation for keywords: only relevant excerpts.
        The whole conversation of a running Claude Code session (name or id from
        list_active_sessions); use it instead of reading long output when looking for something
        said earlier."""
        target = find_live(session)
        turns = transcripts.recent_turns(read_transcript(target["sessionId"], target.get("cwd")), None)
        size = max(max_chars, 40)
        matches = transcripts.search_turns(turns, query, limit=5, context_chars=size // 2 - 10)
        for match in matches:
            match["text"] = transcripts.clip(match["text"], size)
        return {"session": target["name"], "total_turns": len(turns), "matches": matches}

    @mcp.tool(title="Check for news since last time", annotations=READ_ONLY)
    def whats_new(cursor: str | None = None) -> dict[str, Any]:
        """News since you last asked: a session finished, needs input, started or ended.
        Returns only new events and a cursor to pass next time; call without a cursor first,
        cheap enough to poll."""
        check()
        after = _parse_cursor(cursor)
        items = watcher.feed_after(after) if after is not None else []
        latest = max(after or 0, watcher.last_seq())
        return {
            "cursor": str(latest),
            "events": [{k: v for k, v in e.items() if k not in ("seq",)} for e in items],
        }

    @mcp.tool(title="Show the tree of sessions", annotations=READ_ONLY)
    def session_tree() -> dict[str, Any]:
        """The sessions as a tree, with which ones have messaged each other lately.
        The bridge, the Claude Code sessions running on this machine, helper agents, and for
        each which others it has sent messages to in the last day (talks_to); `version` changes
        when the shape changes, and whats_new reports that as kind "tree_changed"."""
        return tree.build()

    @mcp.tool(title="Check the bridge's health", annotations=READ_ONLY)
    def health() -> dict[str, Any]:
        """The bridge's own state: version, uptime, calls and errors per tool, folders.
        Since when and which version, memory, the last restart, calls/errors/latency per tool
        since start, the latest errors, courier, watcher, and the root and granted folders."""
        snap = METRICS.snapshot()
        courier = transcripts.shared_courier()
        return {
            "now": {
                "up_since": started_at,
                "version": running_version,
                "memory_rss_mb": metrics.memory_rss_mb(),
                "last_restart": {"at": restart["at"], "reason": restart["reason"]},
                "restarts_total": restart["total"],
            },
            "tools": snap["tools"],
            "errors": snap["errors"],
            "courier": {"warm": courier.warm, "last_delivery_ms": courier.last_delivery_ms},
            "watcher": {
                "last_check_at": last_check[0] if last_check else None,
                "tree_version": tree_version[0] if tree_version else tree.build()["version"],
            },
            "access": {"root": str(manager.root), "granted": [str(p) for p in manager.access.granted()]},
        }

    return mcp


def build_app(server: MCPServer, public_hosts: list[str] | None = None) -> ASGIApp:
    """HTTP app. Auth (OAuth plus the static token) comes from the server's provider."""
    from starlette.responses import PlainTextResponse

    @server.custom_route("/healthz", methods=["GET"])
    async def healthz(request):  # noqa: ARG001
        return PlainTextResponse("ok")

    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", *(public_hosts or [])]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=[f"https://{h}" for h in public_hosts or []] + ["http://127.0.0.1:*", "http://localhost:*"],
    )
    return PublicClientMetadata(server.streamable_http_app(transport_security=security))


def configure_logging(log_file: Path) -> logging.Handler:
    """Log to the console and to `log_file`, rotated; returns the file handler."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    logging.getLogger().setLevel(logging.INFO)  # basicConfig leaves the level alone once handlers exist
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=LOG_BYTES, backupCount=LOG_KEPT, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    return handler


def serve_forever(cfg: Config) -> None:
    """Run the bridge with a checked configuration until stopped."""
    transcripts.configure_courier(cfg.courier_name)
    store = Store(cfg.db)
    manager = SessionManager(
        store,
        project_root=cfg.root,
        denied=access.default_denied(),
        exempt=access.default_exempt(),
        new_mode=cfg.new_session_mode,
    )
    if cfg.transport == "stdio":
        build_server(manager).run("stdio")
        return

    import uvicorn

    token = cfg.token or ""
    # The token doubles as the login secret on the OAuth consent page and as a
    # static bearer token for local MCP clients.
    oauth = OAuthProvider(
        store, login_secret=token, public_url=cfg.public_url, static_token=token, access_ttl=cfg.access_ttl
    )
    restart = metrics.restart_info("~/.local/state/claude-voice/restarts")
    app = build_app(build_server(manager, oauth=oauth, watch_interval=5.0, restart=restart), cfg.public_hosts)
    configure_logging(cfg.db.parent / LOG_FILE)
    # No log config of its own, so uvicorn's lines, the request log included, reach the file.
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info", log_config=None)
