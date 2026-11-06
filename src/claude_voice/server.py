"""The MCP face of the bridge: tools a voice assistant can call, over stdio or HTTP."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import logging.handlers
import os
import re
import secrets
import subprocess
import sys
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

from . import (
    access,
    api,
    config,
    detail,
    doctor,
    events,
    fork,
    gitstatus,
    launch,
    metrics,
    permissions,
    plugins,
    procs,
    roles,
    software,
    titles,
    transcripts,
    updater,
    usage,
)
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
# The Claude desktop app names a side chat, opened from a session for a quick aside, "btw: <question>".
SIDE_CHAT_PREFIX = "btw:"
# A wait asked without a length: Claude's voice mode moves a call still running after about
# 10 seconds to the background and asks the assistant to speak, so a default wait ends first.
WAIT_SECONDS = 8.0
# The model a session starts on when none is asked for. Claude Code's own default follows the
# account, which can be a model the owner doesn't want for everyday work.
DEFAULT_MODEL = "opus"
# Voice mode also moves a call to the background when the owner talks over it, a few seconds
# in, and later delivers its result inside a call the assistant seems to have written itself,
# carrying a field only the app may write; assistants copy it to fetch results, and are
# refused. A result that took this long carries a reminder, where the copied example is.
LATE_SECONDS = 1.0
LATE_NOTE = (
    "If this came late, in a call carrying _deferred_result_for: only the app writes that "
    "field; a pending result arrives by itself."
)
# The tools that wait on a session; another call on that session while one runs is told so.
WAITING_TOOLS = ("wait_for_session", "ask_active_session")
PENDING_NOTE = "A wait on this session is still running; its result arrives by itself."
# The header naming a call's MCP session, and how many sessions are remembered as having the manual.
MCP_SESSION_HEADER = "mcp-session-id"
GUIDED_KEPT = 1000
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
# descriptions never repeat the manual's rules as reminders. The app's agents load tools by search
# and may never call guide, so the manual also comes with the first result of any MCP session that
# hasn't read it (result_notes).
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
  machine, so keep what you read back short. When a session reports work the
  owner already approved, done as agreed, confirm it in a few words and bring
  up only what differs from the plan, surprises, or decisions needed.

The relay's scope
- Relaying to a session that owns a piece of work, pass on the owner's words
  and the context it lacks. How to do the work (its structure, wording, plan)
  is that session's call. An idea of your own goes along only as a suggestion
  marked as yours, and a change within that session's plan goes to it rather
  than being done by you on the side.

Waiting
- After you send a session a message, wait for its reply; don't ask the owner
  whether to. Call wait_for_session again while it says "still_working",
  until the reply comes: the owner interrupts if they have something to say.
- Wait in silence: nothing between waits, no heads-up, no progress, even when
  the app asks you to say it is taking a while. Speak once, with the reply.
- When the owner speaks during a wait, answer them. The call they talked over
  keeps running and its result arrives by itself; then wait again.
- A session's reply is final only when its turn has ended (reply_final true,
  or status "idle" with turns_left 0). Before that it is its latest message so
  far: don't report it as finished, or as cut off.

Sessions
- New work: start_active_session, or attach_conversation to resume an earlier
  conversation, then give it its task. Never ask a desktop session to start
  one: the app refuses a start from a relayed message and puts a card in front
  of the owner.
- A session's first task is a self-contained brief: the goal, what is known
  so far (problems found, the owner's words), the notes or files involved, and
  how much care it deserves. Unless the owner said otherwise, ask it to
  propose changes before applying them; relay its proposal to the owner. Sessions the bridge starts or resumes are
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
    access_ttl: int = ACCESS_TTL
    ui_origins: list[str] = field(default_factory=list)
    ui_dir: Path | None = None
    new_session_mode: str = "default"


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
    cfg.ui_origins = [o.strip().rstrip("/") for o in env.get("CLAUDE_VOICE_UI_ORIGINS", "").split(",") if o.strip()]
    ui_dir = env.get("CLAUDE_VOICE_UI_DIR", "").strip()
    if ui_dir:
        cfg.ui_dir = Path(ui_dir).expanduser()
        if not cfg.ui_dir.is_dir():
            raise ConfigError(f"CLAUDE_VOICE_UI_DIR is not a directory: {cfg.ui_dir}. Build the UI there, or unset it.")
    ttl = env.get("CLAUDE_VOICE_ACCESS_TOKEN_SECONDS", "").strip()
    if ttl:
        # RULE: an access-token lifetime that is not a whole number of at least a minute stops the bridge
        if not ttl.isdigit() or int(ttl) < MIN_ACCESS_TTL:
            raise ConfigError(
                f"CLAUDE_VOICE_ACCESS_TOKEN_SECONDS is {ttl!r}; use a whole number of seconds, "
                f"at least {MIN_ACCESS_TTL} (an hour is 3600, a week 604800)."
            )
        cfg.access_ttl = int(ttl)
    cfg.new_session_mode = env.get("CLAUDE_VOICE_PERMISSION_MODE", "").strip() or "default"
    if cfg.new_session_mode not in permissions.MODES:
        raise ConfigError(
            f"CLAUDE_VOICE_PERMISSION_MODE is {cfg.new_session_mode!r}; "
            f"use one of {', '.join(sorted(permissions.MODES))}."
        )
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


REMOTE_ID = re.compile(r"session_[A-Za-z0-9]+")
# A session named by its short number, as said aloud: "3", "nr 3", "nr. 3", "nummer 3", "number 3".
SIMPLE_FOLDER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")  # a new project folder: one plain name
SPOKEN_NUMBER = re.compile(r"(?:nr\.?|nummer|number)?\s*(\d{1,4})", re.IGNORECASE)


def app_url(bridge_session_id: Any) -> str | None:
    """The address that opens a Remote Control session in the Claude app, from the id
    Claude Code records for it; None when the session is not on Remote Control."""
    # RULE: only a well-formed remote control id becomes a link
    if not isinstance(bridge_session_id, str) or not REMOTE_ID.fullmatch(bridge_session_id):
        return None
    return f"https://claude.ai/code/{bridge_session_id}"


VOICE_STORE: list[Store] = []  # the store voice calls are counted in, set by build_server


def _result_chars(result: Any) -> int:
    content = getattr(result, "content", None) or (result.get("content") if isinstance(result, dict) else None) or []
    return sum(
        len(str(getattr(c, "text", None) or (c.get("text") if isinstance(c, dict) else "") or "")) for c in content
    )


def caller_kind(ctx: Any, trace: str | None) -> str:
    """Who called, in a word: the Claude app, the voice app, another program here, or in process."""
    request = getattr(ctx, "request", None)
    if request is None:
        return "in_process"
    return usage.client_of(api.caller_address(getattr(request, "scope", None) or {}), trace) or "local"


def note_voice_call(ctx: Any, tool: str, trace: str | None, result: Any) -> None:
    """Count a call served to the Claude app or the voice app, for the voice estimate."""
    request = getattr(ctx, "request", None)
    if request is None or not VOICE_STORE:
        return  # in-process callers are not voice
    scope = getattr(request, "scope", None) or {}
    client = usage.client_of(api.caller_address(scope), trace)
    if client is None:
        return
    connection = trace if client == "voice_app" else request.headers.get("mcp-session-id") or api.caller_address(scope)
    with contextlib.suppress(Exception):
        usage.record(VOICE_STORE[0], client, str(connection), tool, _result_chars(result))


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
    client_token = transcripts.current_client.set(caller_kind(ctx, trace))
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
        transcripts.current_client.reset(client_token)
    ms = (time.monotonic() - started) * 1000
    if ctx.method == "tools/call":
        api.publish_focus(tool, params.get("arguments"), caller_kind(ctx, trace))
        note_voice_call(ctx, tool, trace, result)
        failed = getattr(result, "is_error", None) or (isinstance(result, dict) and result.get("isError"))
        target = api.session_of(tool, params.get("arguments"))
        log.info(
            "mcp %s -> %s %dms trace=%s%s from=%s",
            what,
            "error" if failed else "ok",
            ms,
            trace or "-",
            f" session={target}" if target else "",
            caller_kind(ctx, trace),
        )
        error = None
        if failed:
            content = getattr(result, "content", None) or (result.get("content") if isinstance(result, dict) else None)
            first = content[0] if content else None
            error = str(getattr(first, "text", None) or (first.get("text") if isinstance(first, dict) else first) or "")
        METRICS.record(tool, ms, error=error, trace_id=trace)
    elif ctx.request_id is not None:
        log.info("mcp %s", what)
    return result


def mcp_session(ctx: Any) -> str | None:
    """The MCP session a call belongs to: one per conversation in the Claude app, which opens
    each with an initialize handshake. None for a client without one, such as a stateless exchange."""
    headers = getattr(getattr(ctx, "request", None), "headers", None)
    return headers.get(MCP_SESSION_HEADER) if headers is not None else None


def result_notes() -> Any:
    """Middleware adding notes to results: the manual to the first result of an MCP session that
    hasn't called guide, LATE_NOTE to a slow call's result, and PENDING_NOTE to a call on a
    session that a wait is still running on."""
    waits: dict[str, int] = {}
    # MCP sessions that read the manual or were handed it; the newest kept, oldest dropped.
    guided: dict[str, None] = {}

    async def note(ctx, call_next):
        params = ctx.params if isinstance(ctx.params, dict) else {}
        args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        connection = mcp_session(ctx) if ctx.method == "tools/call" else None
        # A first call to guide itself gets the same manual under the same key: nothing changes.
        unguided = bool(connection) and connection not in guided
        session = str(args.get("session") or "") if ctx.method == "tools/call" else ""
        pending = bool(session) and waits.get(session, 0) > 0
        waiting = params.get("name") in WAITING_TOOLS and bool(session)
        if waiting:
            waits[session] = waits.get(session, 0) + 1
        started = time.monotonic()
        try:
            result = await call_next(ctx)
        finally:
            if waiting:
                waits[session] -= 1
        if ctx.method != "tools/call" or not isinstance(result, dict) or result.get("isError"):
            return result
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            return result
        notes = {}
        manual = {}
        if unguided:
            # Handed over first in the result, so it is read before the rest.
            manual["guide"] = INSTRUCTIONS
            guided[str(connection)] = None
            while len(guided) > GUIDED_KEPT:
                del guided[next(iter(guided))]
        if pending:
            notes["pending"] = PENDING_NOTE
        if time.monotonic() - started >= LATE_SECONDS:
            notes["late"] = LATE_NOTE
        if not notes and not manual:
            return result
        noted = {**manual, **structured, **notes}
        text = json.dumps(noted, indent=2, ensure_ascii=False, default=str)
        return {**result, "structuredContent": noted, "content": [{"type": "text", "text": text}]}

    return note


async def wake_interrupted(
    store: Store,
    background_jobs: Callable[[], Awaitable[list[dict[str, Any]]]],
    was_live: set[str],
    start_background: Callable[..., Awaitable[str | None]],
) -> list[str]:
    """Wake the sessions the bridge started or resumed that the last shutdown interrupted.

    A shutdown stops every background session, and Claude Code shows each as failed; nothing
    restarts them. The bridge stops with them, so one counts as interrupted when it was running
    at the bridge's last look (`was_live`) and is now failed. Windows' boot time can't tell: with
    Fast Startup a shutdown resumes the old kernel. Each is woken in place with its saved
    options, Remote Control included, and logged. Returns their names.
    """
    woken = []
    for job in await background_jobs():
        sid, cwd = str(job.get("sessionId")), str(job.get("cwd") or "")
        if job.get("state") != "failed" or not cwd or sid not in was_live or not store.was_started(sid):
            continue
        name = str(job.get("name") or sid)
        try:
            await start_background(cwd, "", resume=sid, wake=True)
        except Exception as exc:
            log.info("could not wake %s (%s) after the restart: %s", name, sid, exc)
            continue
        log.info("woke %s (%s), interrupted by the last shutdown", name, sid)
        woken.append(name)
    return woken


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
    background_jobs: Callable[[], Awaitable[list[dict[str, Any]]]] = transcripts.background_jobs,
    stop_background: Callable[[str], Awaitable[None]] = transcripts.stop_background,
    stop_window: Callable[[int], None] = transcripts.stop_window,
    trust_folder: Callable[[Path], None] = transcripts.trust_folder,
    plugin_approvals: Path | None = None,
    plugin_client: Callable[[Any], Any] | None = None,
    open_in_desktop: Callable[[str, str, str], Awaitable[None]] = transcripts.open_in_desktop,
    start_seconds: float = 20.0,
    list_prs: Callable[[str], list[dict[str, Any]]] | None = None,
    ui_origins: list[str] | None = None,
    summarize: Callable[[str, str | None, str | None], Awaitable[str]] = transcripts.summarize,
    digest_chars: int = 150_000,
    side_answer: Callable[[str, str, str | None], Awaitable[str]] | None = None,
    side_chars: int = 8_000,
    poll_seconds: float = 1.0,
    receive_seconds: float = 15.0,
    projects_dir: Path = PROJECTS_DIR,
    roles_file: Path | None = roles.USER_FILE,
    doctor_host: doctor.Host | None = None,
    bridge_port: int = 8811,
    statusline_dir: Path = usage.STATUSLINE_DIR,
    update_log: Path = updater.LOG,
    launch_update: Callable[[Path, Path], None] | None = None,
    bridge_folder: Path | None = None,
    repo_config: config.Config | None = None,
    find_program: Callable[[str], str | None] = procs.find,
    desktop_env: Mapping[str, str] | None = None,
    ci_status: Callable[[str], dict[str, Any] | None] = software.latest_run,
    read_prompt: Callable[[str], str | None] = transcripts.pending_prompt,
) -> MCPServer:
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
    host: doctor.Host = doctor_host or doctor.RealHost()
    repos = repo_config or config.load(config.FILE, own_clone=roles.own_clone())

    def within_budget(seconds: float) -> float:
        """How long a call may wait: what it asked for, never past five minutes or the time
        budget of the client calling (the Claude app drops a call after about 30 seconds)."""
        budget = repos.budgets.seconds(transcripts.current_client.get())
        # RULE: a wait never runs past the callers time budget
        return max(min(seconds, 300, budget), 0)

    start_settings = launch.Settings(manager.store, repos.sessions)
    desktop = os.environ if desktop_env is None else desktop_env

    def how_to_start(
        folder: str, name: str, start_in: str | None, remote_control: bool | None
    ) -> tuple[dict[str, Any], str, str | None]:
        """What to start a session with, where it ends up, and a note when that is not
        where it was asked for: the settings, unless this one start asks otherwise."""
        chosen = start_settings.get()
        where = start_in or chosen["start_in"]
        remote = chosen["remote_control"] if remote_control is None else remote_control
        if where != "terminal":
            return {"remote_control": remote, "terminal": None}, "background", None
        terminal, opened = launch.terminal_for(folder, name, repos.sessions.terminal_command, find_program, desktop)
        if terminal is None:
            note = "No terminal could be opened here, so it started in the background."
            return {"remote_control": remote, "terminal": None}, "background", note
        return {"remote_control": remote, "terminal": terminal}, str(opened), None

    def role_table() -> dict[str, dict[str, Any]]:
        contribute = repos.paths.contribute
        return roles.load(
            roles_file, {"folder": str(contribute) if contribute else "", "upstream": repos.repos.claude_voice}
        )

    VOICE_STORE[:] = [manager.store]
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
        if api.HUB.listening():
            fleet_recap()  # notes each session's state, and announces the ones that changed
            update_state()  # and each step of an update under way
            announce_new_turns()

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

            # What ran at the last look, read before the watcher's first look replaces it.
            was_live = watcher.last_seen_live()
            task = asyncio.create_task(watch())
            # The service keeps the side-question reader ready, so the first answer is quick too.
            warming = asyncio.create_task(transcripts.shared_reader().warm())
            warming.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            # The served bridge starts at logon: bring back what the last shutdown stopped.
            waking = asyncio.create_task(wake_interrupted(manager.store, background_jobs, was_live, start_background))
            waking.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
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
        middleware=[log_requests, result_notes()],
        lifespan=lifespan,
    )
    tool = titles.titled(mcp, titles.load())  # each tool's display title, from the shared table
    if oauth is not None:
        mcp.custom_route("/oauth/consent", methods=["GET"])(oauth.consent_page)
        mcp.custom_route("/oauth/consent", methods=["POST"])(oauth.consent_submit)
    store = manager.store

    def by_number(session: str) -> str:
        """`session`, or the id of the session its number names: "3", "nr 3", "nummer 3"."""
        match = SPOKEN_NUMBER.fullmatch(str(session).strip())
        if not match:
            return session
        return store.session_by_number(int(match.group(1))) or session

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

    @tool(annotations=READ_ONLY)
    def guide() -> dict[str, Any]:
        """Read this first, before any other call: the rules every agent here follows.
        The same text as the server's instructions; read it again when unsure how a role or a
        rule works."""
        return {"guide": INSTRUCTIONS}

    @tool(annotations=READ_ONLY)
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

    state_seen: dict[str, tuple[str, str]] = {}  # session id -> (state, since)

    def live_state(status: str, sid: str, cwd: str) -> tuple[str, str | None]:
        """Claude Code's status, named as one of the fixed states, with what a wait is for."""
        if status == "waiting":
            if transcripts.open_question(sid, cwd, projects_dir):
                return "answer", "question"
            if transcripts.pending_tool(sid, cwd, projects_dir):
                return "approval", "approval"
            return "input", "input"
        # Claude Code says "shell" when Claude has finished its turn while a command it started
        # runs on in the background: the session takes a new message at once, so it is idle.
        return {"busy": "working", "idle": "idle", "shell": "idle", "moved": "moved"}.get(status, "idle"), None

    git_seen: dict[str, Any] = {}

    def git_of(sid: str, folder: str) -> dict[str, Any] | None:
        """The folder's git status; open streams hear when it changes."""
        status = gitstatus.cached(folder)
        shape = {k: v for k, v in (status or {}).items() if k != "checked_at"}
        if sid in git_seen and git_seen[sid] != shape:
            api.HUB.publish("git", {"session": sid, "git": status})
        git_seen[sid] = shape
        return status

    def observe(sid: str, name: str, state: str, doing: str) -> str:
        """When `sid` entered `state`; tells open streams when it changes."""
        now = datetime.fromtimestamp(manager.clock(), UTC).isoformat(timespec="seconds")
        seen = state_seen.get(sid)
        if seen is not None and seen[0] == state:
            return seen[1]
        state_seen[sid] = (state, now)
        if seen is not None:
            api.HUB.publish(
                "status",
                {
                    "session": sid,
                    "name": name,
                    "state": state,
                    "previous": seen[0],
                    "since": now,
                    "doing": doing,
                    "at": now,
                },
            )
        return now

    sending: dict[str, list[dict[str, Any]]] = {}  # messages still being delivered, by session id
    in_flight: set[asyncio.Future[Any]] = set()

    active_plugins: list[plugins.Plugin] = []
    for spec in repos.plugins:
        if not plugins.approved(spec, plugin_approvals):
            log.info("plugin %s is not approved; run: claude-voice plugins approve %s", spec.name, spec.name)
            continue
        plugin = plugins.Plugin(spec, plugin_client or plugins.CLIENT)
        listing = plugin.listing()
        if not plugins.inputs_approved(spec, listing, plugin_approvals):
            log.info("plugin %s's tools take new inputs; run: claude-voice plugins approve %s", spec.name, spec.name)
            continue
        names = plugins.offer(mcp, plugin, listing)
        active_plugins.append(plugin)
        log.info("plugin %s offers %s", spec.name, ", ".join(names))

    @tool(annotations=READ_ONLY)
    def fleet_recap() -> dict[str, Any]:
        """Every running session with its status and one line on what it is doing.
        Answers "what are my sessions up to?" without reading each one; kind is "desktop" (open
        in the desktop app), "terminal" or "background" (no terminal window; the bridge's own
        are on Remote Control and in the desktop sidebar), and side_chat marks a quick aside the
        owner opened from another session in the desktop app (named "btw: ..."), which gets no
        work unless asked."""
        sessions = []
        live_by_id = {str(d.get("sessionId")): d for d in live_sessions()}
        live_rows = running()
        numbers = store.number_sessions([str(r["claude_session_id"]) for r in live_rows])
        for row in live_rows:
            if "access" in row:
                # Not open to the bridge: nothing is read from it.
                item = {k: v for k, v in row.items() if k != "claude_session_id"}
                number = numbers.get(str(row["claude_session_id"]))
                sessions.append({"id": row["claude_session_id"], "number": number, **item, "doing": ""})
                continue
            d = live_by_id.get(str(row["claude_session_id"]), {})
            if row["status"] == "moved":
                where = row["moved_to"]["name"] or "another session"
                doing = f"moved to {where}"
            else:
                doing = transcripts.doing_line(str(row["claude_session_id"]), str(d.get("cwd")), projects_dir)
            state, waiting_kind = live_state(str(row["status"]), str(row["claude_session_id"]), str(d.get("cwd")))
            item = {
                "id": row["claude_session_id"],
                "number": numbers.get(str(row["claude_session_id"])),
                "name": row["name"],
                "kind": session_kind({**d, "kind": row["kind"]}),
                "project": row["project"],
                "status": row["status"],
                "state": state,
                "state_since": observe(str(row["claude_session_id"]), str(row["name"]), state, doing),
                "waiting_kind": waiting_kind,
                "git": git_of(str(row["claude_session_id"]), str(d.get("cwd"))) if d.get("cwd") else None,
                "context": usage.context(statusline_dir, str(row["claude_session_id"])),
                "app_url": app_url(d.get("bridgeSessionId")),
                "doing": doing,
                "minutes_since_update": row["minutes_since_update"],
                "sending": list(sending.get(str(row["claude_session_id"]), [])),
            }
            for key in ("waiting_for", "question", "moved_to", "side_chat"):
                if key in row:
                    item[key] = row[key]
            sessions.append(item)
        return {"sessions": sessions}

    @tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    async def start_active_session(
        project: str,
        name: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        start_in: str | None = None,
        remote_control: bool | None = None,
        create_folder: bool = False,
    ) -> dict[str, Any]:
        """Start new work: a session in a project, on Remote Control on phone and PC.
        A project from list_projects; the session is named `name` or after the project, listed at
        once so ask_active_session reaches it, and runs on Remote Control with no window unless
        the settings say otherwise; `start_in` ("terminal" for a window on the PC) and
        `remote_control` change that once.
        `create_folder=true` first makes the project folder under the root when it does not
        exist; only for a plain name (letters, digits, '.', '_', '-'), never by default.
        Optional: `model` (an alias such as opus or sonnet, or a full name) and `effort` (low,
        medium, high, xhigh or max); left out, the model is opus and the effort Claude Code's
        default."""
        # RULE: only a well-formed model name and a known effort reach Claude Code
        if model is not None and not transcripts.MODEL_NAME.fullmatch(model):
            raise ToolError(f"Not a model name: {model!r}. Use an alias such as opus, or a full model name.")
        if effort is not None and effort not in transcripts.EFFORTS:
            raise ToolError(f"Unknown effort {effort!r}; use one of: {', '.join(transcripts.EFFORTS)}.")
        folder_created = False
        if create_folder:
            # RULE: a new folder must be a simple name under the root
            if not SIMPLE_FOLDER.fullmatch(project):
                raise ToolError(f"{project!r} is not a simple name; use letters, digits, '.', '_' or '-' only.")
            new = manager.root / project
            if not new.exists():
                new.mkdir()  # never over something that exists
                folder_created = True
                # RULE: only a folder the bridge just made is trusted for claude code
                trust_folder(new)
        try:
            path = manager.resolve_project(project)
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        # RULE: starting the same session twice gives back the one already running
        same = next(
            (d for d in live_sessions() if name and d.get("name") == name.strip() and d.get("cwd") == str(path)), None
        )
        if same is not None:  # a retry after an answer lost on the way must not open a second window
            return {
                "name": same.get("name"),
                "claude_session_id": same.get("sessionId"),
                "project": under_root(str(path)),
                "status": same.get("status") or "idle",
                "already_running": True,
                "folder_created": folder_created,
            }
        out = await start_listed(path, name, start_in, remote_control, model=model or DEFAULT_MODEL, effort=effort)
        # Claude Code accepts any model name at start and refuses an unknown one only
        # in its first reply, so a start alone does not prove the model works.
        model_note = "Claude Code checks the model only at the first reply; rely on the session after it."
        notes = [n for n in (out.pop("note", None), model_note if model else None) if n]
        return {**out, "folder_created": folder_created, **({"note": " ".join(notes)} if notes else {})}

    async def start_listed(
        path: Path, name: str | None, start_in: str | None = None, remote_control: bool | None = None, **options: Any
    ) -> dict[str, Any]:
        """Start a session in `path` under a free name, where the settings say unless this start
        asks otherwise, and wait until it is listed.

        A woken session runs in the background with its own saved options and name, so it is
        found by its id instead.
        """
        wake = options.get("wake")
        taken = {str(d.get("name")) for d in live_sessions()}
        wanted = "" if wake else (name or "").strip()[:60] or f"{path.name}-{secrets.token_hex(2)}"
        while wanted in taken:
            wanted = f"{wanted}-{secrets.token_hex(2)}"
        if start_in is not None and start_in not in config.LAUNCHES:
            raise ToolError(f"start_in is one of: {', '.join(config.LAUNCHES)}")
        how: dict[str, Any] = {}
        started_in, where_note = "background", None
        if not wake:
            how, started_in, where_note = how_to_start(str(path), wanted, start_in, remote_control)
        try:
            given = {k: v for k, v in options.items() if v is not None}
            printed = await start_background(str(path), wanted, **given, **how) or ""
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
                    "started_in": started_in,
                    **({"note": where_note} if where_note else {}),
                    **({"claude_code_note": " ".join(notes)} if notes else {}),
                }
            await asyncio.sleep(0.2)
        raise ToolError(f"{wanted or name} was started but did not appear among the running sessions")

    def unused_name(wanted: str) -> str:
        taken = {str(d.get("name")) for d in live_sessions()}
        while wanted in taken:
            wanted = f"{wanted}-{secrets.token_hex(2)}"
        return wanted

    async def appeared(name: str, folder: Path) -> dict[str, Any]:
        """The started session once Claude Code lists it, recorded so the bridge may stop it."""
        deadline = time.monotonic() + start_seconds
        while time.monotonic() < deadline:
            found = next((d for d in live_sessions() if d.get("name") == name and d.get("cwd") == str(folder)), None)
            if found:
                store.record_started(str(found.get("sessionId")), name, str(folder))
                return found
            await asyncio.sleep(0.2)
        raise ToolError(f"{name} was started but did not appear among the running sessions")

    async def launch_role(role: str, task: str | None) -> dict[str, Any]:
        table = role_table()
        if role not in table:
            raise LookupError(f"No role {role!r}; there are: {', '.join(sorted(table))}")
        spec = table[role]
        blockers = doctor.before_role(host, spec) if spec.get("guard") else []
        # RULE: a contribute session does not start while something it needs is missing
        if blockers:
            raise ToolError(
                "Not started; first: " + "; ".join(f"{b['title']}: {b['detail']}. Fix: {b['fix']}" for b in blockers)
            )
        folder = roles.folder_for(spec)
        name = unused_name(str(spec.get("name") or role))
        how, _, _ = how_to_start(str(folder), name, None, None)
        try:
            await start_background(
                str(folder),
                name,
                prompt=roles.first_message(spec, folder, task),
                settings=roles.guard_settings(spec),
                **how,
            )
        except Exception as exc:
            raise ToolError(f"Claude Code could not start a session: {exc}") from None
        found = await appeared(name, folder)
        store.set_setting(f"role:{role}", {"name": name, "id": str(found.get("sessionId"))})
        return {"role": role, "name": name, "claude_session_id": found.get("sessionId"), "status": found.get("status")}

    @tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    async def start_role(role: str, task: str | None = None) -> dict[str, Any]:
        """Start a background session for a role, such as "contribute".
        A contribute session changes claude-voice and opens a pull request; `task` is what the owner
        wants. Reach it with ask_active_session."""
        try:
            return await launch_role(role, task)
        except LookupError as exc:
            raise ToolError(str(exc)) from None

    transcript_sizes: dict[str, int] = {}

    def announce_new_turns() -> None:
        """Tell open streams which running session wrote something new. A transcript is only
        read once it has grown, so a quiet session costs one look at its size."""
        for d in live_sessions():
            sid, cwd = str(d.get("sessionId")), str(d.get("cwd"))
            path = projects_dir / project_key_for_directory(cwd) / f"{sid}.jsonl"
            try:
                size = path.stat().st_size
            except OSError:
                continue
            seen = transcript_sizes.get(sid)
            transcript_sizes[sid] = size
            if seen is None or size <= seen:
                continue
            turns = numbered_turns(d)
            if turns:
                api.HUB.publish("output", {"session": sid, "index": turns[-1]["index"], "role": turns[-1]["role"]})

    update_steps_seen: list[int] = [0]

    def update_state() -> dict[str, Any]:
        """The latest update's log; open streams hear of each new step."""
        try:
            body = json.loads(update_log.expanduser().read_text())
        except (OSError, ValueError):
            body = {"running": False, "steps": [], "result": None}
        steps = body.get("steps") or []
        if len(steps) < update_steps_seen[0]:
            update_steps_seen[0] = 0  # a new run began
        for step in steps[update_steps_seen[0] :]:
            api.HUB.publish("update", step)
        update_steps_seen[0] = len(steps)
        return {**body, "running": bool(body.get("running")) and updater.running(update_log)}

    def start_update() -> dict[str, Any]:
        if updater.running(update_log):
            raise RuntimeError("An update is already running.")
        folder = bridge_folder or repos.paths.claude_voice
        if folder is None:
            raise LookupError("This bridge does not run from a git clone, so it cannot update itself.")
        (launch_update or launch_updater)(folder, update_log.expanduser())
        return {
            "started": True,
            "note": "The bridge restarts if the update works, so the voice drops for a few seconds.",
        }

    def launch_updater(folder: Path, log: Path) -> None:
        """Start the updater outside this service, so it survives the restart it causes."""
        systemd_run = procs.find("systemd-run")
        if not systemd_run:
            raise RuntimeError("Updating needs systemd-run, to run outside the bridge's own service.")
        unit = f"claude-voice-update-{int(time.time())}"
        command = [systemd_run, "--user", "--collect", "--quiet", f"--unit={unit}", sys.executable]
        command += ["-m", "claude_voice.updater", "--repo", str(folder), "--log", str(log), "--port", str(bridge_port)]
        subprocess.run(command, check=True, capture_output=True, timeout=30)

    @tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
    def update_bridge() -> dict[str, Any]:
        """Update claude-voice to the latest version, rolling back if anything fails.
        It pulls, installs, tests and restarts.
        Say first that the voice drops for a few seconds while it restarts."""
        try:
            return start_update()
        except (LookupError, RuntimeError) as exc:
            raise ToolError(str(exc)) from None

    def update_changes_now() -> dict[str, Any]:
        folder = bridge_folder or repos.paths.claude_voice
        if folder is None:
            raise LookupError("No claude-voice clone is known; set [paths] claude_voice in the settings file.")
        return updater.changes(Path(folder))

    @tool(annotations=READ_ONLY)
    def update_changes() -> dict[str, Any]:
        """What updating claude-voice would bring: the new commits on GitHub.
        Grouped by kind (new, fixed, and so on), newest last.
        Say the groups in a sentence or two."""
        try:
            return update_changes_now()
        except LookupError as exc:
            raise ToolError(str(exc)) from None

    software_seen: dict[str, Any] = {}

    def software_now() -> dict[str, Any]:
        """Both repositories' measures and checks, kept for ten minutes: git and GitHub are slow."""
        if software_seen and time.monotonic() - software_seen["at"] < 600:
            return software_seen["report"]
        listed = (
            ("claude-voice", repos.repos.claude_voice, bridge_folder or repos.paths.claude_voice),
            ("claude-voice-ui", repos.repos.claude_voice_ui, repos.paths.claude_voice_ui),
        )
        report = {
            "as_of": datetime.now(UTC).isoformat(timespec="seconds"),
            "repos": [
                software.repo_report(name, f"https://github.com/{repo}", folder, ci_status(repo))
                for name, repo, folder in listed
            ],
        }
        software_seen.update(at=time.monotonic(), report=report)
        return report

    def bridge_now() -> dict[str, Any]:
        folder = bridge_folder or repos.paths.claude_voice
        return {
            "version": running_version,
            "git": gitstatus.cached(folder) if folder else None,
            "started_at": api.STARTED_AT,
        }

    def doctor_report() -> dict[str, Any]:
        report = doctor.examine(host, role_table().get("contribute"), port=bridge_port)
        report["checks"] += [*config.check_paths(repos), launch.check(repos.sessions, find_program, desktop)]
        return report

    @tool(annotations=READ_ONLY)
    def check_dependencies() -> dict[str, Any]:
        """Which of the bridge's dependencies are missing, and the exact fix for each.
        With what each costs: Claude Code's sign-in, git, the GitHub CLI, uv, Tailscale, the tailnet
        port, the status line figures, and the contribute session's clone."""
        return doctor_report()

    def roles_now() -> dict[str, Any]:
        live = {str(d.get("sessionId")): d for d in live_sessions()}
        out = []
        for name, spec in role_table().items():
            seen = store.get_setting(f"role:{name}")
            d = live.get(seen["id"]) if seen else None
            session = {"name": d.get("name"), "id": seen["id"], "status": d.get("status")} if d and seen else None
            out.append({**roles.public(name, spec), "session": session})
        return {"roles": out}

    @tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    async def stop_active_session(name: str) -> dict[str, Any]:
        """Stop a session the bridge started; any other session is refused.
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
            if target.get("kind") == "bg":
                await stop_background(sid)
            else:  # a window the bridge opened: ending its Claude Code closes it
                stop_window(int(target.get("pid") or 0))
        except Exception as exc:
            raise ToolError(f"Claude Code could not stop {name}: {exc}") from None
        deadline = time.monotonic() + start_seconds
        while any(d.get("sessionId") == sid for d in live_sessions()):
            if time.monotonic() > deadline:
                raise ToolError(f"{name} was asked to stop but is still running")
            await asyncio.sleep(0.2)
        return {"name": name, "status": "stopped"}

    @tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
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

    @tool(annotations=READ_ONLY)
    def list_active_sessions() -> dict[str, Any]:
        """Every session running now, with status; a waiting one carries its prompt.
        Claude Code sessions on this machine, busy, idle or waiting; kind "interactive" is one
        the user types in, "bg" one with no terminal window (the bridge's own are on Remote
        Control); side_chat marks a desktop side chat ("btw:
        ..."), which gets no work unless asked. A session outside the open folders has access
        "folder_not_open" (open it with request_access, which the owner approves in their app) or
        "folder_closed" (never opened; a refusal names the closed folder), and only its name,
        folder and status; access is absent when the session is open to the bridge, and a
        session waiting on a permission prompt carries it as prompt, which the owner
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

    @tool(annotations=READ_ONLY)
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

    @tool(
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
    )
    async def attach_conversation(
        claude_session_id: str, project: str | None = None, label: str | None = None
    ) -> dict[str, Any]:
        """Resume an earlier conversation on Remote Control; then talk to it.
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
            out = await start_listed(path, label, resume=claude_session_id, wake=True)
        else:
            grant = manager.grant_for(claude_session_id, str(path), manager.new_mode)
            out = await start_listed(
                path, label, resume=claude_session_id, permission_mode=grant.mode, add_dirs=grant.dirs
            )
        if out["claude_session_id"] != claude_session_id:
            out["copied_from"] = claude_session_id
        return out

    def find_any_live(session: str) -> dict[str, Any]:
        session = by_number(session)
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

    @tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
    def request_access(session: str | None = None, folder: str | None = None) -> dict[str, Any]:
        """Open a folder to the bridge for good, a session's or by path; owner approves.
        Give one of: `session`, a listed session, to open its folder; or `folder`, a full path,
        to open a folder no session runs in yet, so start_active_session can work there. The
        folder and everything under it can then be read, messaged and worked in. The app asks
        the owner before every call, with no "Always allow"."""
        if (session is None) == (folder is None):
            raise ToolError("Give one of session or folder.")
        if folder is not None:
            if not Path(folder).is_absolute():
                raise ToolError(f"{folder!r}: give the folder's full path.")
            target, d = folder, {}
        else:
            d = find_any_live(str(session))
            target = str(d.get("cwd"))
        try:
            opened = manager.access.grant(target)
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        return {**({"session": d.get("name")} if d else {}), "folder": str(opened), "granted": True}

    @tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    def revoke_access(folder: str) -> dict[str, Any]:
        """Close a folder opened with request_access; health lists the granted ones."""
        try:
            closed = manager.access.revoke(folder)
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        return {"folder": str(closed), "granted": False}

    @tool(
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
        sid = str(target["sessionId"])
        before = len(numbered_turns(target))
        budget = repos.budgets.seconds(transcripts.current_client.get())
        delivery = asyncio.ensure_future(deliver(target["name"], f"{message}\n\n{RELAYED_MESSAGE_NOTE}"))
        try:
            out = await asyncio.wait_for(asyncio.shield(delivery), budget)
        except TimeoutError:
            # RULE: a delivery slower than the clients budget is answered as sending
            return {**sending_on(sid, str(target["name"]), message, delivery), "next_after": before - 1}
        if not out.get("delivered"):
            raise ToolError(f"Could not deliver: {out.get('detail') or 'unknown reason'}")
        return {"session": target["name"], "status": "delivered", "delivered": True, "next_after": before - 1}

    def sending_on(sid: str, name: str, message: str, delivery: asyncio.Future[Any]) -> dict[str, Any]:
        """Leave a slow delivery running; it shows on the session's card until Claude Code has sent
        it, and open streams hear how it went."""
        item = {"id": secrets.token_hex(6), "text": transcripts.clip(message, 80), "at": api.now_iso()}
        sending.setdefault(sid, []).append(item)
        in_flight.add(delivery)

        def done(task: asyncio.Future[Any]) -> None:
            in_flight.discard(task)
            with contextlib.suppress(ValueError):
                sending.get(sid, []).remove(item)
            result = task.result() if not task.cancelled() and task.exception() is None else {}
            delivered = bool(result.get("delivered"))
            detail = str(result.get("detail") or ("" if delivered else "the courier broke"))
            outcome = "delivered" if delivered else f"not delivered: {detail}"
            log.info("message %s to %s -> %s", item["id"], name, outcome)
            report = {"session": sid, "id": item["id"], "delivered": delivered, "detail": detail}
            api.HUB.publish("message_sent", report)

        delivery.add_done_callback(done)
        return {"session": name, "status": "sending", "delivered": None, "id": item["id"]}

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

    def live_by_pid(pid: Any) -> dict[str, Any] | None:
        """The running session with this process id now, or None once it has exited."""
        return next((d for d in live_sessions() if d.get("pid") == pid), None)

    @tool(annotations=READ_ONLY)
    async def wait_for_session(
        session: str, wait_seconds: float = WAIT_SECONDS, after: int | None = None, max_chars: int = 600
    ) -> dict[str, Any]:
        """Wait 8 s, up to 20, for a session's turn to end or need input; then its reply.
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

        pid, blocked, done = target.get("pid"), None, False
        deadline = asyncio.get_running_loop().time() + within_budget(wait_seconds)
        while (now := live_by_pid(pid)) is not None:
            target, blocked = now, waiting(now)
            done = blocked is not None or (now.get("status") == "idle" and (after is None or bool(said(now))))
            if done or asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(poll_seconds)
        if now is None:
            status = "session_ended"
        else:
            status = blocked["status"] if blocked else ("idle" if done else "still_working")
        written = [] if now is None else said(target)
        size = max(max_chars, 20)
        new = [
            {**t, "text": transcripts.clip(t["text"], size)} for t in (written if after is not None else written[-1:])
        ]
        return {
            "status": status,
            "session": target["name"],
            "reply": new[-1]["text"] if new else "",
            "reply_final": status in ("idle", "needs_input", "needs_choice") and bool(new),
            "session_ended": now is None,
            "turns": new,
            "next_after": new[-1]["index"] if new else (after if after is not None else -1),
            **({"question": blocked["question"]} if status == "needs_choice" and blocked else {}),
        }

    @tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    async def ask_active_session(
        session: str, message: str, wait_seconds: float = WAIT_SECONDS, max_chars: int = 600
    ) -> dict[str, Any]:
        """Ask a session and wait (8 s, up to 20) for its answer; only when needed now.
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
                "reply_final": False,
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
                "reply_final": False,
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
        deadline = started + within_budget(wait_seconds)
        probe = message.strip()[:80]
        while True:
            now = live_by_pid(pid)
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

    @tool(annotations=READ_ONLY)
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
        only once the session's status is "idle"; until then `latest` holds its newest reply, to
        say first when far behind); the first turn that doesn't fit is still started. A turn
        longer than max_chars, or than the room left, is cut and says so (cut,
        length), and `turn` (its index) with `offset` reads it whole, page by page via
        next_offset."""
        target = find_live(session)
        turns = every = numbered_turns(target)
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

        left = 0
        if after is None:
            picked = taken(turns[::-1], forward=False)[::-1]
        else:
            later = [t for t in turns if t["index"] > after]
            picked = taken(later, forward=True)
            left = len(later) - len(picked)  # read on with next_after until this is 0
        turns = []
        for t, room in picked:
            t["length"] = len(t["text"])
            t["cut"] = t["length"] > room
            t["text"] = transcripts.clip(t["text"], room)
            turns.append(t)
        next_after = turns[-1]["index"] if turns else (after if after is not None else -1)
        out = {
            "session": target["name"],
            "status": target.get("status"),
            "turns": turns,
            "next_after": next_after,
            "turns_left": left,
        }
        # RULE: a read that leaves turns for later still shows the newest reply
        newest = next((t for t in reversed(every) if t["role"] == "assistant"), None)
        if left and newest is not None:
            out["latest"] = {
                "index": newest["index"],
                "role": "assistant",
                "text": transcripts.clip(newest["text"], size),
            }
        return out

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

    @tool(annotations=READ_ONLY)
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
        await asyncio.wait({task}, timeout=within_budget(wait_seconds))
        head = {"fork_id": fork_id, "session": entry["session"]}
        if not task.done():
            return {**head, "status": "still_working", "answer": ""}
        return {**head, **task.result()}

    @tool(annotations=READ_ONLY)
    async def ask_forked_session(session: str, question: str, wait_seconds: float = WAIT_SECONDS) -> dict[str, Any]:
        """Ask the side session, a read-only fork that reads code; waits 8 s, up to 20.
        The side session is a new read-only fork of a running session's whole conversation that
        can read the project's files, while the session itself keeps working, untouched; each
        question forks anew, from the latest state. It waits up to the caller's time budget (20 s
        for the Claude app): status "answered" (report it as the side
        session's), "still_working" (read it later with read_forked_session(fork_id)),
        "timed_out", or "error" with `detail`."""
        return await collect_fork(start_fork(session, question), wait_seconds)

    @tool(annotations=READ_ONLY)
    async def message_forked_session(session: str, question: str) -> dict[str, Any]:
        """Send the side session a question and return; read it with read_forked_session.
        Like message_active_session: a new read-only fork of the session's conversation starts
        working on the question at once, and read_forked_session(fork_id) gets its answer
        later."""
        fork_id = start_fork(session, question)
        return {"fork_id": fork_id, "session": forks[fork_id]["session"], "status": "started"}

    @tool(annotations=READ_ONLY)
    async def read_forked_session(fork_id: str, wait_seconds: float = WAIT_SECONDS) -> dict[str, Any]:
        """The side session's answer, waiting 8 s, up to 20; call again on still_working.
        status "answered", "still_working" (call again), "timed_out" (it worked five minutes and
        was stopped) or "error" with `detail`; answers are kept for an hour, and forgotten when
        the bridge restarts."""
        return await collect_fork(fork_id, wait_seconds)

    @tool(annotations=READ_ONLY)
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

    @tool(annotations=READ_ONLY)
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

    @tool(annotations=READ_ONLY)
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

    @tool(annotations=READ_ONLY)
    def session_tree() -> dict[str, Any]:
        """The sessions as a tree, with which ones have messaged each other lately.
        The bridge, the Claude Code sessions running on this machine, helper agents, and for
        each which others it has sent messages to in the last day (talks_to); `version` changes
        when the shape changes, and whats_new reports that as kind "tree_changed"."""
        return tree.build()

    @tool(annotations=READ_ONLY)
    def health() -> dict[str, Any]:
        """The bridge's own state: version, uptime, calls and errors per tool, folders.
        Since when and which version, memory, the last restart, calls/errors/latency per tool
        since start, the latest errors, courier, watcher, and the root and granted folders."""
        snap = METRICS.snapshot()
        courier = transcripts.shared_courier()
        offered = [p.status() for p in active_plugins]
        return {
            "plugins": offered,
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

    # -- the session the voice talks to, and the companion page that picks it -----------

    def resolve_session(session: str) -> dict[str, Any]:
        """A running session or one of the bridge's own, by name or id, as the page shows it."""
        session = by_number(session)
        for row in fleet_recap()["sessions"]:
            if session in (row["id"], row["name"]):
                return {"id": row["id"], "number": row.get("number"), "name": row["name"], "kind": row["kind"]}
        raise LookupError(f"No session called {session!r}")

    def set_active(session: str, by: str) -> dict[str, Any]:
        found = resolve_session(session)
        active = {**found, "set_by": by, "set_at": datetime.now(UTC).isoformat(timespec="seconds")}
        store.set_setting("active_session", active)
        api.HUB.publish("active", active)
        log.info("active -> %s by %s", found["name"], by)
        return active

    def clear_active() -> None:
        store.clear_setting("active_session")
        api.HUB.publish("active", None)
        log.info("active -> none by page")

    @tool(annotations=READ_ONLY)
    def get_active_session() -> dict[str, Any]:
        """The session the owner chose to talk to: send there by default.
        Chosen on the companion page or by voice.
        Null fields when none is chosen."""
        return store.get_setting("active_session") or {"id": None, "name": None, "set_by": None, "set_at": None}

    @tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    def set_active_session(session: str) -> dict[str, Any]:
        """Make `session` the one the owner talks to, shown on the companion page.
        `session` is a name or id from fleet_recap.
        Call it when the owner switches session by voice."""
        try:
            return set_active(session, "voice")
        except LookupError as exc:
            raise ToolError(str(exc)) from None

    pr_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    async def prs_for(folder: str) -> list[dict[str, Any]]:
        cached = pr_cache.get(folder)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        lister = list_prs or transcripts.open_pull_requests
        try:
            found = await asyncio.to_thread(lister, folder)
        except Exception:
            found = []
        pr_cache[folder] = (time.monotonic(), found)
        return found

    def lookup(session: str) -> dict[str, Any]:
        """The running session named `session` (name, id or number)."""
        session = by_number(session)
        for d in live_sessions():
            if session in (d.get("name"), str(d.get("sessionId"))) and under_root(d.get("cwd")):
                return d
        raise LookupError(f"No session called {session!r}")

    summaries = detail.Summaries(store, summarize, api.HUB.publish)

    def session_detail(session: str) -> dict[str, Any]:
        """A session's own page: context, tool calls, helpers, timeline and its kept summary.
        Read from its transcript; it never makes a summary."""
        live = lookup(session)
        sid, cwd = str(live.get("sessionId")), str(live.get("cwd"))
        numbers = store.number_sessions([str(d.get("sessionId")) for d in live_sessions()])
        entries = detail.read(projects_dir / project_key_for_directory(cwd) / f"{sid}.jsonl")
        return {
            "session": sid,
            "number": numbers.get(sid),
            "name": live.get("name"),
            "context": usage.context(statusline_dir, sid),
            **detail.digest(entries),
            **summaries.state(sid),
        }

    def summary_task(session: str, language: str | None) -> tuple[dict[str, Any], asyncio.Task[str | None]]:
        live = lookup(session)
        kept, _ = conversation_end(live, digest_chars)
        return live, summaries.start(str(live.get("sessionId")), "\n".join(kept), language)

    async def request_summary(session: str, language: str | None) -> dict[str, Any]:
        live, _ = summary_task(session, language)
        return {"session": str(live.get("sessionId")), "state": "generating"}

    @tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    async def refresh_session_summary(session: str | None = None, language: str | None = None) -> dict[str, Any]:
        """Make a fresh summary of a running session, read by a small model.
        The chosen one if `session` is left out, in `language` if given (e.g. "norsk"); the companion
        app's session page shows it too. Takes about ten seconds."""
        if not session:
            session = str((store.get_setting("active_session") or {}).get("id") or "")
            if not session:
                raise ToolError("No session is chosen; name one.")
        try:
            live, task = summary_task(session, language)
        except LookupError as exc:
            raise ToolError(str(exc)) from None
        summary = await task
        sid = str(live.get("sessionId"))
        if summary is None:
            raise ToolError(f"The summary failed: {summaries.state(sid)['summary_error']}")
        return {"session": live.get("name"), "summary": summary, "summary_at": summaries.state(sid)["summary_at"]}

    def api_output(session: str, limit: int, after: int | None) -> dict[str, Any]:
        live = lookup(session)
        out = read_session_output(str(live.get("sessionId")), limit=limit, after=after)
        return {k: out[k] for k in ("status", "turns", "next_after", "turns_left")}

    def api_pending(session: str) -> dict[str, Any]:
        live = lookup(session)
        waiting = live.get("status") == "waiting"
        question = (
            transcripts.open_question(str(live.get("sessionId")), str(live.get("cwd")), projects_dir)
            if waiting
            else None
        )
        return {"waiting": waiting, "question": question, "approvals": []}

    async def api_prs(session: str) -> dict[str, Any]:
        live = lookup(session)
        return {"prs": await prs_for(str(live.get("cwd")))}

    if oauth is not None:

        async def check_token(token: str) -> bool:
            return await oauth.load_access_token(token) is not None

        api.EventApi(
            store,
            oauth.login_secret,
            oauth.public_url,
            check_token,
            sessions=fleet_recap,
            get_active=lambda: store.get_setting("active_session"),
            set_active=set_active,
            clear_active=clear_active,
            output=api_output,
            pending=api_pending,
            prs=api_prs,
            news=whats_new,
            roles=roles_now,
            doctor=doctor_report,
            update_state=update_state,
            start_update=start_update,
            bridge=bridge_now,
            usage=lambda: {
                "plan": (current := usage.plan(statusline_dir)),
                "warning": usage.warning(current),
                "voice": usage.voice(store),
            },
            start_role=launch_role,
            settings=start_settings,
            software=software_now,
            detail=session_detail,
            summary=request_summary,
            update_changes=update_changes_now,
            ui_origins=ui_origins,
        ).routes(mcp)

    return mcp


def build_app(
    server: MCPServer,
    public_hosts: list[str] | None = None,
    ui_origins: list[str] | None = None,
    ui_dir: Path | None = None,
) -> ASGIApp:
    """HTTP app. Auth (OAuth plus the static token) comes from the server's provider.

    A companion UI's built files in `ui_dir` are served at /ui/; a UI served elsewhere
    may call the event API across sites only from an origin in `ui_origins`."""
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
    app = server.streamable_http_app(transport_security=security)
    if ui_dir is not None:
        from starlette.staticfiles import StaticFiles

        app.mount("/ui", StaticFiles(directory=str(ui_dir), html=True), name="ui")
    wrapped: ASGIApp = api.TailnetOnly(PublicClientMetadata(app))
    if ui_origins:
        from starlette.middleware.cors import CORSMiddleware

        # RULE: only an origin named in the settings may call across sites
        wrapped = CORSMiddleware(
            wrapped,
            allow_origins=list(ui_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT"],
            allow_headers=["Authorization", "Content-Type"],
        )
    return wrapped


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
        grant_for=permissions.session_grant,
    )
    if cfg.transport == "stdio":
        build_server(manager).run("stdio")
        return

    token = cfg.token or ""
    # The token doubles as the login secret on the OAuth consent page and as a
    # static bearer token for local MCP clients.
    oauth = OAuthProvider(
        store, login_secret=token, public_url=cfg.public_url, static_token=token, access_ttl=cfg.access_ttl
    )
    restart = metrics.restart_info("~/.local/state/claude-voice/restarts")
    server = build_server(
        manager, oauth=oauth, watch_interval=5.0, restart=restart, ui_origins=cfg.ui_origins, bridge_port=cfg.port
    )
    app = build_app(server, cfg.public_hosts, ui_origins=cfg.ui_origins, ui_dir=cfg.ui_dir)
    configure_logging(cfg.db.parent / LOG_FILE)
    web_server(app, cfg.host, cfg.port).run()


SHUTDOWN_SECONDS = 5  # how long a stop waits for open requests; event streams never end


def web_server(app: Any, host: str, port: int) -> Any:
    """The HTTP server, which stops within seconds of being asked: it waits that long for
    open requests, then closes the rest, so a restart never hangs on a stream."""
    import uvicorn

    # No log config of its own, so uvicorn's lines, the request log included, reach the file.
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        log_config=None,
        # RULE: the bridge stops within seconds even with a stream open
        timeout_graceful_shutdown=SHUTDOWN_SECONDS,
    )
    return uvicorn.Server(config)
