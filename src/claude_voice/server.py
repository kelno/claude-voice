"""The MCP face of the bridge: tools a voice assistant can call, over stdio or HTTP."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import logging.handlers
import os
import re
import secrets
import time
import weakref
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from claude_agent_sdk import SDKSessionInfo, project_key_for_directory
from claude_agent_sdk import list_sessions as list_claude_sessions
from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.subscriptions import ToolsListChanged
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl, Field
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.types import ASGIApp

from . import (
    access,
    api,
    channel,
    config,
    confirm,
    confirm_page,
    detail,
    devmode,
    doctor,
    events,
    fleet,
    fork,
    gitstatus,
    launch,
    machines,
    metrics,
    passkeys,
    plugin_desk,
    plugins,
    procs,
    renames,
    roles,
    session_modes,
    software,
    tailnet,
    titles,
    transcripts,
    updater,
    usage,
    work,
)
from .channel_hub import ChannelHub, ancestor_session, started_with_channel
from .metrics import METRICS
from .oauth import ACCESS_TTL, MIN_ACCESS_TTL, SCOPE, OAuthProvider, PublicClientMetadata
from .sessions import SessionManager
from .store import Store
from .terminals import NOT_LOCAL, Terminals
from .tree import PROJECTS_DIR, SessionTree, session_kind

MIN_TOKEN_CHARS = 32
READ_ONLY = ToolAnnotations(read_only_hint=True)
COURIER_PREFIX = "claude-voice-msg-"
# Added after every message the relay sends into a running session: the session can't know
# otherwise that nobody reads its screen and that a SendMessage reply goes nowhere.
RELAYED_MESSAGE_NOTE = (
    "[From the claude-voice bridge: this message comes from the relay, the agent working with "
    "this session through the bridge for the owner: a voice assistant, or another session. The "
    "owner sees neither this message nor your reply unless the relay "
    "passes them on, and the relay has no inbox: answer in your reply text, not with SendMessage. "
    "Address the relay, not the owner, and make the reply self-contained: name things instead of "
    "pointing back to text it may not have. For the owner's own OK, which a relayed message "
    "can't give, ask with the bridge's confirm_ask tool.]"
)
# The Claude desktop app names a side chat, opened from a session for a quick aside, "btw: <question>".
SIDE_CHAT_PREFIX = "btw:"
# A wait asked without a length: Claude's voice mode moves a call still running after about
# 10 seconds to the background and asks the assistant to speak, so a default wait ends first.
# With the server proxy's overhead an 8 s wait crossed it once (2026-11-06), so 5 s.
WAIT_SECONDS = 5.0
# The model a session starts on when none is asked for. Claude Code's own default follows the
# account, which can be a model the owner doesn't want for everyday work.
DEFAULT_MODEL = "opus"

# What a parameter several tools share means, said once and used by every tool that takes it.
# The model sees a parameter's own description next to it, while a result's schema never
# reaches it (Claude Code and the Claude app, tested 2026-11-11): so what concerns one
# parameter goes there, and what a result means stays in the tool's description.
SESSION_PARAM = "A running session's name or id, from list_active_sessions."
WAIT_PARAM = (
    "Keep the default, 5 s, in the Claude app, which sends a call past about 10 s to the "
    "background. At most the caller's limit (20 s for the Claude app)."
)
MAX_CHARS_PARAM = "How long each message or excerpt in the result may be before it is cut."
LANGUAGE_PARAM = 'The language to answer in, such as "norsk".'
CREATE_FOLDER_PARAM = (
    "Only when asked: true first makes the folder under the root, for a plain name (letters, digits, '.', '_', '-')."
)
OPEN_FOLDER_PARAM = (
    "A full path; Claude Code then trusts it too, the whole repository if it is inside one: "
    "tell the owner. For a folder no session runs in yet, so start_active_session can work "
    "there."
)
SUMMARY_PARAM = (
    "Exactly what is approved, for someone with no context, in at most 300 characters. Name the "
    "file, text, command or permission string."
)
WAIT_AFTER_PARAM = (
    "The next_after a send returned, to wait for the reply to it; left out, all you haven't "
    "read of that session. With it, the wait holds even if the session hasn't picked the "
    "message up yet; without it, unread counts from where you last read it in any "
    "conversation (from this call if you never did), and an idle session returns at once."
)

# Voice mode also moves a call to the background when the owner talks over it, a few seconds
# in, and later delivers its result inside a call the assistant seems to have written itself,
# carrying a field only the app may write; assistants copy it to fetch results, and are
# refused. A result that took this long carries a reminder, where the copied example is: a rule
# kept in the owner's memory alone still slipped, and the app refuses the copy before the bridge
# sees it, so the reminder can't wait for the slip.
LATE_SECONDS = 1.0
LATE_NOTE = (
    "A call carrying _deferred_result_for was inserted by the app, not written by you; "
    "sending that field fails. Pending results arrive by themselves."
)
# The tools that wait on a session; another call on that session while one runs is told so.
WAITING_TOOLS = ("wait_for_session", "ask_active_session")
PENDING_NOTE = "A wait on this session is still running; its result arrives by itself."
# Upstream tools the owner switched off in the fork: their code stays, as close to upstream's as
# it can be, but no client is offered them (UPSTREAM.md, "Upstream features, and how they're set here").
SWITCHED_OFF = frozenset(
    {
        "get_active_session",
        "set_active_session",
        "refresh_session_summary",
        # Upstream's plugin tools: this PC runs no plugins, so they would only lengthen the list.
        "plugin_restart",
        "developer_mode_request",
        "developer_mode_confirm",
        "developer_mode_off",
        "plugin_approve_request",
        "plugin_approve_confirm",
    }
)
# The header naming a call's MCP session, and how many sessions are remembered as having the manual.
MCP_SESSION_HEADER = "mcp-session-id"
GUIDED_KEPT = 1000
# Tools after which a conversation follows a session: by its `session` argument and the turn its
# result reached (`next_after`), or by the `claude_session_id` it started or resumed.
READ_MARKING_TOOLS = ("message_active_session", "read_session_output", "wait_for_session", "ask_active_session")
FOLLOWING_TOOLS = ("start_active_session", "attach_conversation")
# Where the sessions the relay watches are kept, so a bridge restart doesn't drop them.
WATCHED_SETTING = "watched_sessions"
GUIDE_NOTE = "This is the bridge's guide in full, the same text the guide tool returns: no need to call guide."
# How much text a read of a session's turns returns when no count is asked for: many short
# progress turns fit, a few long reports don't flood the reply.
READ_BUDGET = 3_000
# Reading forward, a turn that doesn't fit the budget still shows at least this much of its
# start, so an unread reply can't be mistaken for no reply.
READ_START = 300
# A finished turn's last message, a report most often, comes back whole up to this length.
FINAL_CHARS = 12_000
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
# here (the guide tool's first line, a clearer rule) first, so the tool descriptions avoid
# repeating the manual's rules as reminders; one that slipped despite the manual may get a reminder
# where it's needed, with a comment saying why (AGENTS.md). The app's agents load tools by search
# and may never call guide, so the manual also comes with the first result of any MCP session that
# hasn't read it (result_notes).
INSTRUCTIONS = """\
Read this first, before any other call: how the claude-voice bridge is used.

Roles
- This guide is for the relay. A session that only answers relayed messages
  needs only the note on each message, not this guide. A session that starts or
  messages other sessions through the bridge is a relay for that work and reads
  it.
- Voice relay: the owner talks to you, a voice assistant, and mostly works
  without looking at a screen, so keep to what works by ear; when a step needs
  the screen (a link to open, a passkey to confirm), say so aloud. You are the
  relay. The Claude Code session doing the work at the owner's
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
- Sessions answer you in self-contained text written for an agent: condense
  it, and keep what you say aloud short, since the owner is usually away from
  the machine. When a session reports work the owner already approved, done
  as agreed, confirm it in a few words and raise only what differs, surprises,
  or decisions needed.

The relay's scope
- Relaying to a session that owns a piece of work, pass on the owner's words
  and the context it lacks. How to do the work (its structure, wording, plan,
  and its process: commits, pushes, checks, which its own rules govern) is
  that session's call. Add no limit or permission the owner didn't give. An
  idea of your own goes along only as a suggestion marked as yours, and a
  change within that session's plan goes to it rather than being done by you
  on the side.
- When a session already holds the context for new work, have it write the
  brief, start the new session itself if it can (see Sessions), and send the
  brief; then ask it for the new session's name. It stays reachable for the
  new session's questions. If it can't start one, start it yourself with the
  first message it wrote: the relay only works from summaries, so its briefs
  lose details and add mistakes.

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

News
- A result may start with news: an entry for each session you watch, with
  its state and what's new, or "nothing new". You watch the sessions you
  started, resumed, messaged, asked (ask_active_session) or read
  (read_session_output, wait_for_session); watch_session adds one, such as one
  the owner hands you, or drops one. The set is the relay's, kept across
  voice conversations and bridge restarts, so it may name sessions an earlier
  conversation touched.
- News is four things only: "reply to you", once a session's turn after your
  message has ended (its answers to the owner's own typing aren't one); "the
  owner typed here", when the owner wrote to it directly; a permission prompt
  waiting, shown as just that, watched or not; "ended", named once, then
  dropped ("ended before you read its reply": attach_conversation resumes it,
  to read it). Progress lines aren't news; read_session_output still shows
  them. "read with after=N" says where to read from.
- What's new stays in the entry until you read the session
  (read_session_output, wait_for_session or ask_active_session returned it,
  even cut), or the prompt is answered. Tell the owner once, at your next
  reply, not during a silent wait. Read a session first,
  before telling the owner what it said, and before writing to it again.
  "the owner typed here" asks nothing aloud: read it to catch up, and don't
  tell the owner what they wrote. A waiting prompt is the owner's to answer,
  on the phone or at the PC: say what it asks (list_active_sessions shows
  it), before a wait if one is coming.
- When a topic with the owner wraps up, check the news yourself, without
  asking: any call returns it, list_active_sessions is a cheap one. Speak
  only if something is new. About to offer the owner to check on the
  sessions? That's the moment to check instead.
- Tell news one thing at a time: first what needs the owner (an action or a
  decision), the rest once they ask or that thing is done.

Sessions
- New work: start_active_session in a folder (list_folders), or
  attach_conversation to resume an earlier conversation, then give it its
  task. A "project" the owner names is a folder to the bridge; claude.ai
  Projects, the chat workspaces, are out of its reach.
  A session the bridge started can start one itself with the bridge's tools.
  A desktop session (one the owner opened in the desktop app) can't: the app
  refuses a start from a relayed message and puts a card in front of the
  owner, so never ask one to.
- A session's first task is a self-contained brief: the goal, what is known
  so far (problems found, the owner's words), the notes or files involved, and
  how much care it deserves. Unless the owner said otherwise, ask it to
  propose changes before applying them; relay its proposal to the owner.
  Sessions the bridge starts or resumes are on Remote Control and in the
  desktop app's sidebar, so the owner can open them on the phone or at the PC
  and take over.
- side_chat marks a "btw: ..." aside the owner opened from a desktop session:
  give it no work unless asked.
- A refusal from Claude Code's auto mode is not a prompt: the session asks for
  the owner's OK with confirm_ask, and you hand the owner its link.

Missing tools
- The app caches this connector's tool list. If a tool named here isn't in
  yours, search for it by its exact name; if it still isn't there, tell the
  owner the tool list needs a refresh in claude.ai Settings → Connectors.
- This bridge is the owner's own software, kept up by their agents: when a
  tool gets in your way or lacks something, proposing a change to the bridge
  is a fair suggestion, and feedback you log can name that change as a lead.
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


def load_config(env: Mapping[str, str], transport: str, *, need_root: bool = True) -> Config:
    """The bridge's settings from `env`. A command that never touches the work folders, such as
    `claude-voice passkey`, passes need_root=False, so a shell without CLAUDE_VOICE_ROOT can run it."""
    # RULE: an api key in the environment stops the bridge from starting
    if env.get("ANTHROPIC_API_KEY") and env.get("CLAUDE_VOICE_ALLOW_API_KEY") != "1":
        raise ConfigError(
            "ANTHROPIC_API_KEY is set, so Claude would bill the API instead of your "
            "subscription. Unset it, or set CLAUDE_VOICE_ALLOW_API_KEY=1 if that is intended."
        )
    root = Path(env.get("CLAUDE_VOICE_ROOT", "~/src")).expanduser()
    if need_root and not root.is_dir():
        raise ConfigError(
            f"CLAUDE_VOICE_ROOT is not a directory: {root}. Make it with `mkdir -p {root}`, or set "
            "CLAUDE_VOICE_ROOT to the folder that holds your work folders."
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
    if cfg.new_session_mode not in session_modes.MODES:
        raise ConfigError(
            f"CLAUDE_VOICE_PERMISSION_MODE is {cfg.new_session_mode!r}; "
            f"use one of {', '.join(sorted(session_modes.MODES))}."
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
SIMPLE_FOLDER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")  # a new folder: one plain name
SPOKEN_NUMBER = re.compile(r"(?:nr\.?|nummer|number)?\s*(\d{1,4})", re.IGNORECASE)


def app_url(bridge_session_id: Any) -> str | None:
    """The address that opens a Remote Control session in the Claude app, from the id
    Claude Code records for it; None when the session is not on Remote Control."""
    # RULE: only a well-formed remote control id becomes a link
    if not isinstance(bridge_session_id, str) or not REMOTE_ID.fullmatch(bridge_session_id):
        return None
    return f"https://claude.ai/code/{bridge_session_id}"


VOICE_STORE: list[Store] = []  # the store voice calls are counted in, set by build_server
# Clients connected the older way (an initialize handshake), told when the tool list
# changes; one is known once it has made a call. Newer clients listen on the server's
# subscription stream instead.
LISTENERS: weakref.WeakSet[Any] = weakref.WeakSet()


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
    with contextlib.suppress(TypeError):
        if (session := getattr(ctx, "session", None)) is not None:
            LISTENERS.add(session)
    token = transcripts.current_trace.set(trace)
    client_token = transcripts.current_client.set(caller_kind(ctx, trace))
    request = getattr(ctx, "request", None)
    seen = (
        confirm.caller_from(request.headers, api.caller_address(request.scope), meta) if request else confirm.Caller()
    )
    caller_token = confirm.current_caller.set(seen)
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
        confirm.current_caller.reset(caller_token)
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


def result_notes(
    news: Callable[[str, str, dict[str, Any], dict[str, Any]], str | None] | None = None,
    claude_code_transcript: Callable[[], Path | None] | None = None,
) -> Any:
    """Middleware adding notes to results: the manual to the first result of a conversation that
    hasn't called guide (a Claude app conversation by its MCP session, a Claude Code session by
    the transcript holding the call, given again after that transcript shows a compaction), the
    `news` line for that MCP session, LATE_NOTE to a slow call's result, and PENDING_NOTE to a
    call on a session that a wait is still running on."""
    waits: dict[str, int] = {}
    # Conversations that read the manual or were handed it, each with how far its transcript was
    # read (Claude Code sessions; 0 for an MCP session); the newest kept, oldest dropped.
    guided: dict[str, int] = {}

    def needs_manual(connection: str | None) -> str | None:
        """The key to hand the manual over under, when this call's conversation needs it."""
        if connection:
            return None if connection in guided else connection
        path = claude_code_transcript() if claude_code_transcript else None
        if path is None:
            return None
        key = f"transcript:{path}"
        size, compacted = transcripts.compacted_since(path, guided.get(key))
        # RULE: a Claude Code session gets the guide on its first call, and again after a compaction
        fresh = key not in guided or compacted
        guided[key] = size
        return key if fresh else None

    async def note(ctx, call_next):
        params = ctx.params if isinstance(ctx.params, dict) else {}
        args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        connection = mcp_session(ctx) if ctx.method == "tools/call" else None
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
        # Looked up once the call is made, so a Claude Code session's transcript holds it; a first
        # call to guide itself gets the same manual under the same key: nothing changes.
        unguided = needs_manual(connection)
        if unguided:
            # Handed over first in the result, so it is read before the rest; a call to guide
            # itself needs no note that it could have been skipped.
            if params.get("name") != "guide":
                manual["guide_note"] = GUIDE_NOTE
            manual["guide"] = INSTRUCTIONS
            guided.setdefault(unguided, 0)
            while len(guided) > GUIDED_KEPT:
                del guided[next(iter(guided))]
        if news is not None and connection:
            line = news(str(connection), str(params.get("name") or ""), args, structured)
            if line:
                manual["news"] = line
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


def load_watched(store: Store) -> dict[str, dict[str, Any]]:
    """The sessions the relay watches, as the database kept them; none at first."""
    return store.get_setting(WATCHED_SETTING) or {}


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


BUSY = ("busy", "shell")  # a session's statuses while it works


def _not_sent(target: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    """The answer to a question that was never sent: the session, why, and no reply."""
    return {
        "session": target["name"],
        **outcome,
        "session_ended": False,
        "reply": "",
        "reply_final": False,
        "turns": [],
        "next_after": -1,
    }


def _asked_at(turns: list[dict[str, Any]], probe: str) -> dict[str, Any] | None:
    """Our own message among a session's new turns, or None while it is not in view."""
    return next((t for t in turns if t["role"] == "user" and probe in t["text"]), None)


def _settled(
    now: dict[str, Any],
    new_turns: list[dict[str, Any]],
    probe: str,
    was_busy: bool,
    seen_busy: bool,
    receive_ran_out: bool,
) -> str | None:
    """What one look at an asked session shows: answered, not_received, waiting, or None to look again."""
    asked = _asked_at(new_turns, probe)
    answers = [t for t in new_turns if t["role"] == "assistant"]
    if was_busy and asked is not None and any(t["index"] > asked["index"] for t in answers):
        # Asked mid-work: its first words after our question are the answer; the
        # rest of its turn belongs to whatever it was doing.
        return "answered"
    replied = seen_busy or bool(answers)
    arrived = replied or asked is not None
    if not arrived and receive_ran_out:
        return "not_received"  # handed over, yet never reached the conversation
    if replied and now.get("status") == "idle":
        return "answered"
    if now.get("status") == "waiting" and arrived:
        return "waiting"
    return None


def _reply_turns(
    fresh: list[dict[str, Any]], probe: str, before: int, status: str, was_busy: bool
) -> tuple[list[dict[str, Any]], int]:
    """The turns that answer our message, and the index they follow; reply_of cuts them."""
    # Only what answers our message counts: what the session wrote after it. Its
    # last words are the reply; the rest of its turn is how it got there.
    anchor = _asked_at(fresh, probe)
    start = anchor["index"] if anchor else before - 1
    new = [t for t in fresh if t["role"] == "assistant" and t["index"] > start]
    if anchor is None and status != "still_working":
        new = new[-1:]  # the message is not in view: only the session's final words
    elif anchor is not None and was_busy and new:
        new = new[:1]
    return new, start


def build_server(
    manager: SessionManager,
    oauth: OAuthProvider | None = None,
    conversations: Callable[..., list[SDKSessionInfo]] = list_claude_sessions,
    live_dir: Path = LIVE_DIR,
    deliver: Callable[[str, str], Awaitable[dict[str, Any]]] = transcripts.deliver,
    channels: Any = None,
    read_transcript: Callable[[str, str | None], list[Any]] | None = None,
    watch_interval: float | None = None,
    restart: dict[str, Any] | None = None,
    start_background: Callable[..., Awaitable[str | None]] = transcripts.start_background,
    background_sessions: Callable[[], Awaitable[set[str]]] = transcripts.background_sessions,
    background_jobs: Callable[[], Awaitable[list[dict[str, Any]]]] = transcripts.background_jobs,
    claude_config: Path = access.CLAUDE_CONFIG,
    stop_background: Callable[[str], Awaitable[None]] = transcripts.stop_background,
    stop_window: Callable[[int], None] = transcripts.stop_window,
    trust_folder: Callable[[Path], None] = transcripts.trust_folder,
    plugin_approvals: Path | None = None,
    plugin_client: Callable[[Any], Any] | None = None,
    machine_folder: Path = machines.FOLDER,
    machine_http: machines.Http = machines.http_client,
    machine_whois: machines.Whois = tailnet.whois,
    start_seconds: float = 20.0,
    list_prs: Callable[[str], list[dict[str, Any]]] | None = None,
    ui_origins: list[str] | None = None,
    summarize: Callable[[str, str | None, str | None], Awaitable[str]] = transcripts.summarize,
    digest_chars: int = 150_000,
    side_answer: Callable[[str, str, str | None], Awaitable[str]] | None = None,
    side_chars: int = 8_000,
    poll_seconds: float = 0.25,
    silent_grace: float = 1.0,
    receive_seconds: float = 15.0,
    projects_dir: Path = PROJECTS_DIR,
    public_url: str = "http://localhost",
    roles_file: Path | None = roles.USER_FILE,
    doctor_host: doctor.Host | None = None,
    bridge_port: int = 8811,
    bridge_host: str = "127.0.0.1",
    switched_off: frozenset[str] = SWITCHED_OFF,
    statusline_dir: Path = usage.STATUSLINE_DIR,
    update_log: Path = updater.LOG,
    launch_update: Callable[..., None] | None = None,
    bridge_folder: Path | None = None,
    repo_config: config.Config | None = None,
    find_program: Callable[[str], str | None] = procs.find,
    desktop_env: Mapping[str, str] | None = None,
    ci_status: Callable[..., dict[str, Any] | None] = software.latest_run,
    kept_levels: Callable[..., list[dict[str, Any]]] = software.kept_levels,
    jobs_dir: Path = transcripts.JOBS_DIR,
    read_prompt: Callable[[str], str | None] = transcripts.pending_prompt,
    work_board: work.Board | None = None,
    terminals: Terminals | None = None,
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
        manager.store, live_dir, manager.root, clock=manager.clock, folder_of=lambda cwd: under_root(cwd)
    )

    def renamed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return renames.apply(rows, manager.store.renamed(), manager.store.old_names())

    tree = SessionTree(
        live_dir,
        manager.root,
        projects_dir=projects_dir,
        clock=manager.clock,
        folder_of=lambda cwd: under_root(cwd),
        rename=renamed,
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
    running_uncommitted = metrics.uncommitted()
    confirmations = confirm.Confirmations(manager.store)
    page_url = (oauth.public_url if oauth is not None else public_url).rstrip("/")
    owner_keys = passkeys.for_url(manager.store, page_url)

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
                    with contextlib.suppress(Exception):
                        await across.refresh()  # the other machines, asked every ten seconds
                    with contextlib.suppress(Exception):
                        await desk.pick_up()  # an approval typed while the bridge runs
                    with contextlib.suppress(Exception):
                        if api.HUB.listening() and (changed := await asyncio.to_thread(board.changed)):
                            api.HUB.publish("work", changed)  # the phone page updates at once
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
        if channels is not None:
            await channels.start()
        try:
            yield {}
        finally:
            if task:
                task.cancel()
            if channels is not None:
                await channels.stop()

    # The sessions the relay watches, by Claude session id: the last turn it has read (`seen`),
    # the turn its last message went in after while no reply to it has been read (`sent`), and
    # whether that reply's turn was seen to end (`replied`). One set for the relay, whichever
    # conversation it calls from, kept in the database: a bridge restart doesn't end the voice
    # call, which runs in the Claude app's cloud, and the relay, in that call or a new one, still
    # needs the reply to the message it sent.
    watched = load_watched(manager.store)
    # Turns per session, kept while its conversation file is unchanged.
    turns_seen: dict[str, tuple[tuple[int, int], list[dict[str, Any]]]] = {}

    def all_turns(sid: str, cwd: str) -> list[dict[str, Any]]:
        path = projects_dir / project_key_for_directory(cwd) / f"{sid}.jsonl"
        try:
            st = path.stat()
            stamp: tuple[int, int] | None = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = None
        if stamp is not None and sid in turns_seen and turns_seen[sid][0] == stamp:
            return turns_seen[sid][1]
        turns = transcripts.recent_turns(read_transcript(sid, cwd), None, origins=True)
        if stamp is not None:
            turns_seen[sid] = (stamp, turns)
        return turns

    def watch_live(d: dict[str, Any], seen: int | None = None, sent: int | None = None) -> None:
        """Watch a session, from the last turn read (its whole history when not given); a message
        sent after turn `sent` waits for its reply."""
        sid = str(d.get("sessionId"))
        if seen is None:
            seen = len(all_turns(sid, str(d.get("cwd")))) - 1
        mark = watched.setdefault(sid, {"seen": seen, "sent": None, "replied": False})
        mark.update(name=d.get("name") or mark.get("name") or sid, cwd=str(d.get("cwd")))
        mark["seen"] = max(mark["seen"], seen)
        if sent is not None:  # -1 too: a message to a session with no turns yet
            mark.update(sent=sent, replied=False)

    def unwatch(session: str) -> str | None:
        """Stop watching a session, by name or id; its name, or None when it wasn't watched."""
        sid = next((k for k, m in watched.items() if session in (k, m.get("name"))), None)
        return watched.pop(sid).get("name") or sid if sid else None

    def news_line(conversation: str, tool: str, args: dict[str, Any], structured: dict[str, Any]) -> str | None:
        """How the watched sessions stand, in one line, and any session waiting on a permission
        prompt; None when there is neither.

        A call that starts, resumes, messages or reads a session watches it. Four things are news:
        a reply to the relay's message, once that turn has ended; something the owner typed in the
        session (Claude Code marks it "human"; not a message from another session, nor a background
        task's completion note); a permission prompt waiting; the session having ended, said once.
        Progress lines are not news: the relay can read them, but naming them every time made a
        busy session drown the rest.
        """
        before = json.dumps(watched, sort_keys=True)
        live = {str(d.get("sessionId")): d for d in live_sessions() if not is_courier(d)}
        watch_after_call(tool, args, structured, live)
        items = []
        for sid, mark in list(watched.items()):
            items.append(watched_entry(sid, mark, live.get(sid)))
        for sid, d in live.items():
            if sid not in watched and session_state(d) == "waiting on a permission prompt":
                items.append(f"{d.get('name') or sid}: waiting on a permission prompt")
        if json.dumps(watched, sort_keys=True) != before:
            manager.store.set_setting(WATCHED_SETTING, watched)
        return " | ".join(items) if items else None

    def watch_after_call(
        tool: str, args: dict[str, Any], structured: dict[str, Any], live: dict[str, dict[str, Any]]
    ) -> None:
        """Watch the session a call started, resumed, messaged, asked or read, from where it read."""
        target = None
        if tool in READ_MARKING_TOOLS and args.get("session"):
            target = next((d for d in live.values() if args["session"] in (d.get("name"), d.get("sessionId"))), None)
        elif tool in FOLLOWING_TOOLS and structured.get("claude_session_id"):
            target = live.get(str(structured["claude_session_id"]))
        reached = structured.get("next_after")
        if target is not None and tool in FOLLOWING_TOOLS:
            watch_live(target)  # a session started or resumed: its history so far isn't news
        elif target is not None and isinstance(reached, int):
            if tool == "message_active_session":
                # A send reads nothing: what came before stays unread, and while an earlier
                # message's reply is unread, the wait for a reply starts from that message.
                mark = watched.get(str(target.get("sessionId")))
                if mark is None:
                    watch_live(target, seen=reached, sent=reached)  # its history so far isn't news
                elif mark.get("sent") is None:
                    watch_live(target, seen=mark["seen"], sent=reached)
            elif tool == "ask_active_session" and not structured.get("reply_final"):
                # Asked, and the answer hadn't come when the call returned: it is a reply to wait for.
                watch_live(target, seen=reached, sent=reached if structured.get("status") == "still_working" else None)
            else:
                watch_live(target, seen=reached)

    def watched_entry(sid: str, mark: dict[str, Any], d: dict[str, Any] | None) -> str:
        """One watched session's entry in the news line; an ended one is dropped after it."""
        name = (d or {}).get("name") or mark.get("name") or sid
        if d is None:
            del watched[sid]  # said once, then dropped, since it can't be read any more
            last = mark.get("sent")
            unread = last is not None and any(
                t["role"] == "assistant" and i > max(last, mark["seen"])
                for i, t in enumerate(all_turns(sid, str(mark.get("cwd"))))
            )
            return f"{name}: ended" + (" before you read its reply" if unread else "")
        mark["name"] = name
        turns = all_turns(sid, str(d.get("cwd") or mark.get("cwd")))
        news, read_from = [], []
        if mark.get("sent") is not None:
            answer = [i for i, t in enumerate(turns) if t["role"] == "assistant" and i > mark["sent"]]
            # Its turn has ended once it rests, or asks its user something (not mid-turn on a
            # prompt), or once a later message came in after its reply.
            ended = session_state(d) in ("idle", "waiting for an answer") or any(
                t["role"] == "user" and answer and i > answer[0] for i, t in enumerate(turns)
            )
            mark["replied"] = mark["replied"] or (bool(answer) and ended)
            if mark["replied"] and any(i > mark["seen"] for i in answer):
                news.append("reply to you")
                read_from.append(max(mark["sent"], mark["seen"]))
            elif mark["replied"]:
                mark.update(sent=None, replied=False)  # read: the message has its answer
        if any(i > mark["seen"] and t.get("origin") == "human" for i, t in enumerate(turns)):
            news.append("the owner typed here")
            read_from.append(mark["seen"])
        if read_from:
            news.append(f"read with after={min(read_from)}")
        state = session_state(d)
        if state == "waiting on a permission prompt":
            return f"{name}: {', '.join([state, *news])}"
        return f"{name} ({state}): {', '.join(news) or 'nothing new'}"

    def session_state(d: dict[str, Any] | None) -> str:
        if d is None:
            return "ended"
        if d.get("status") == "waiting":
            return (
                "waiting on a permission prompt"
                if d.get("waitingFor") == "permission prompt"
                else "waiting for an answer"
            )
        return "idle" if d.get("status") == "idle" else "busy"

    across = fleet.Fleet()  # the other machines, filled in once they are started below
    across.publish = api.HUB.publish
    mcp = MCPServer(
        "claude-voice",
        title="Claude voice bridge",
        version=running_version,
        instructions=INSTRUCTIONS,
        auth_server_provider=oauth,
        auth=auth,
        middleware=[log_requests, result_notes(news_line, lambda: calling_transcript()), fleet.middleware(across)],
        lifespan=lifespan,
    )
    titled = titles.titled(mcp, titles.load())  # each tool's display title, from the shared table

    def tool(**kwargs: Any) -> Callable[[Callable[..., Any]], Any]:
        """Register a tool, unless the fork switched it off: then its code stays, offered to no one."""
        register = titled(**kwargs)
        return lambda fn: fn if fn.__name__ in switched_off else register(fn)

    # The tool list changes when a plugin is started again or approved, so say so up front
    # (tools.listChanged), and tell connected clients each time it does.
    lowlevel = mcp._lowlevel_server
    lowlevel.create_initialization_options = functools.partial(  # type: ignore[method-assign]
        lowlevel.create_initialization_options, NotificationOptions(tools_changed=True)
    )

    async def tools_changed() -> None:
        with contextlib.suppress(Exception):
            await mcp._subscriptions.publish(ToolsListChanged())
        for session in list(LISTENERS):
            try:
                await session.send_tool_list_changed()
            except Exception:  # a client that has gone away is forgotten
                LISTENERS.discard(session)

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
        """Read this first: the relay's rules for using this bridge.
        If a result already carried the guide, skip the call: it is the same text. Read it again
        when unsure how a role or a rule works."""
        return {"guide": INSTRUCTIONS}

    @tool(annotations=READ_ONLY)
    def list_folders(
        query: Annotated[
            str | None,
            Field(description="A word from a folder's name; it also finds folders under the root never used."),
        ] = None,
    ) -> dict[str, Any]:
        """Folders to start a session in: used by Claude Code lately, or found by a word.
        The folders Claude Code was used in during the last 30 days, anywhere on this machine,
        newest first, from its own history; at most 20. `root` is the folder open to the bridge
        without asking, with everything under it. A folder's name is relative to the root when
        under it, else its full path; its access is as list_active_sessions says."""
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
        return {"root": str(manager.root), "folders": [row for _, row in rows[:20]]}

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

    def commit_of(folder: str) -> str | None:
        return ((gitstatus.read(folder) or {}).get("commit") or {}).get("hash")

    def plugin_specs() -> list[config.PluginSpec]:
        """The plugins as the settings file names them now, so a tool added there is seen
        without restarting the bridge; the ones read at start when it is given or unreadable."""
        if repo_config is not None:
            return repos.plugins
        try:
            return config.load(config.FILE, own_clone=roles.own_clone()).plugins
        except config.ConfigError:
            return repos.plugins

    active_plugins = plugins.start_approved(mcp, repos.plugins, plugin_approvals, plugin_client, commit_of)
    developer_mode = devmode.DeveloperMode()
    desk = plugin_desk.Desk(
        mcp,
        active_plugins,
        specs=plugin_specs,
        approvals=plugin_approvals,
        client=plugin_client,
        git=gitstatus.read,
        mode=developer_mode,
        questions=devmode.Questions(),
        announce=tools_changed,
    )
    mcp.plugin_desk = desk  # type: ignore[attr-defined]  # for tests
    across.machines = machines.start_approved(
        mcp, repos.machines, machine_folder, http=machine_http, whois=machine_whois
    )

    @tool(annotations=READ_ONLY)
    def fleet_recap() -> dict[str, Any]:
        """Every running session with its status and one line on what it is doing.
        Answers "what are my sessions up to?" without reading each one; kind is "desktop",
        "terminal" or "background" (no window), and side_chat marks a desktop aside."""
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
                "kind": row["kind"],
                "folder": row["folder"],
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
        folder: Annotated[str, Field(description="A folder from list_folders, by its name there, or a full path.")],
        name: Annotated[
            str | None, Field(description="The session's name; left out, it is named after the folder.")
        ] = None,
        model: Annotated[
            str | None, Field(description="An alias such as opus or sonnet, or a full model name; left out, opus.")
        ] = None,
        effort: Annotated[
            str | None, Field(description="low, medium, high, xhigh or max; left out, Claude Code's default.")
        ] = None,
        start_in: Annotated[
            str | None,
            Field(
                description='"terminal" opens a window on the PC for this one session; left out, the settings decide.'
            ),
        ] = None,
        remote_control: Annotated[
            bool | None,
            Field(description="True or false overrides the settings on Remote Control for this one session."),
        ] = None,
        create_folder: Annotated[
            bool,
            Field(description=CREATE_FOLDER_PARAM),
        ] = False,
    ) -> dict[str, Any]:
        """Start new work: a session in a folder, on Remote Control on phone and PC.
        It is listed at once, so ask_active_session reaches it, and has no window unless the
        settings say otherwise."""
        # RULE: only a well-formed model name and a known effort reach Claude Code
        if model is not None and not transcripts.MODEL_NAME.fullmatch(model):
            raise ToolError(f"Not a model name: {model!r}. Use an alias such as opus, or a full model name.")
        if effort is not None and effort not in transcripts.EFFORTS:
            raise ToolError(f"Unknown effort {effort!r}; use one of: {', '.join(transcripts.EFFORTS)}.")
        folder_created = False
        if create_folder:
            # RULE: a new folder must be a simple name under the root
            if not SIMPLE_FOLDER.fullmatch(folder):
                raise ToolError(f"{folder!r} is not a simple name; use letters, digits, '.', '_' or '-' only.")
            new = manager.root / folder
            if not new.exists():
                new.mkdir()  # never over something that exists
                folder_created = True
                # RULE: only a folder the bridge just made is trusted for claude code
                trust_folder(new)
        try:
            path = manager.resolve_folder(folder)
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
                "folder": under_root(str(path)),
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
        taken = renames.taken(live_sessions())
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
                    "folder": under_root(str(path)),
                    "status": found.get("status") or "idle",
                    "started_in": started_in,
                    **({"note": where_note} if where_note else {}),
                    **({"claude_code_note": " ".join(notes)} if notes else {}),
                }
            await asyncio.sleep(0.2)
        raise ToolError(f"{wanted or name} was started but did not appear among the running sessions")

    def unused_name(wanted: str) -> str:
        taken = renames.taken(live_sessions())
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
    async def start_role(
        role: Annotated[
            str,
            Field(description='The role, such as "contribute".'),
        ],
        task: Annotated[str | None, Field(description="What the owner wants it to do.")] = None,
    ) -> dict[str, Any]:
        """Start a session for a role, such as "contribute", on Remote Control.
        A contribute session changes claude-voice in its own clone and opens a pull request to
        upstream, as its settings say. Returns the session's name: reach it like any session started here, with
        message_active_session, then a wait."""
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

    def start_update(tests: bool = False) -> dict[str, Any]:
        if updater.running(update_log):
            raise RuntimeError("An update is already running.")
        folder = bridge_folder or repos.paths.claude_voice
        if folder is None:
            raise LookupError("This bridge does not run from a git clone, so it cannot update itself.")
        (launch_update or launch_updater)(folder, update_log.expanduser(), tests=tests)
        return {
            "started": True,
            "note": "The bridge restarts if the update works, so the voice drops for a few seconds.",
        }

    def launch_updater(folder: Path, log: Path, tests: bool = False) -> None:
        """Start the updater outside this bridge, so it survives the restart it causes."""
        updater.launch(folder, log, bridge_port, bridge_host, tests=tests)

    @tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
    def update_bridge(
        run_tests: Annotated[
            bool, Field(description="True runs the tests before the restart, for changes not tested yet.")
        ] = False,
    ) -> dict[str, Any]:
        """Update claude-voice to the latest version, rolling back if anything fails.
        It pulls, installs and restarts. Say first that the voice drops for a few seconds while it
        restarts."""
        try:
            return start_update(run_tests)
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
    # Each plugin's own repository joins the two above, on the software panel and the work page.
    plugin_repos = work.plugin_repos(repos.plugins)
    board = work_board or work.Board(
        {"claude-voice": repos.repos.claude_voice, "claude-voice-ui": repos.repos.claude_voice_ui}
        | {name: repo for name, repo, _ in plugin_repos},
        run=software._gh,
    )

    def software_now() -> dict[str, Any]:
        """Both repositories' measures and checks, kept for ten minutes: git and GitHub are slow."""
        if software_seen and time.monotonic() - software_seen["at"] < 600:
            return software_seen["report"]
        # A plugin may follow another branch than main (dreame-mcp's voice plugin lives on "voice").
        followed = {str(Path(spec.cwd)): spec.branch for spec in repos.plugins}
        listed = (
            ("claude-voice", repos.repos.claude_voice, bridge_folder or repos.paths.claude_voice),
            ("claude-voice-ui", repos.repos.claude_voice_ui, repos.paths.claude_voice_ui),
            *plugin_repos,
        )
        report = {
            "as_of": datetime.now(UTC).isoformat(timespec="seconds"),
            "repos": [
                software.repo_report(
                    name,
                    f"https://github.com/{repo}",
                    folder,
                    ci_status(repo, branch=followed.get(str(folder), "main")),
                    kept_levels(repo, branch=followed.get(str(folder), "main")),
                    branch=followed.get(str(folder), "main"),
                )
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
    async def stop_active_session(name: Annotated[str, Field(description=SESSION_PARAM)]) -> dict[str, Any]:
        """Stop a session the bridge started; any other session is refused.
        A refused one gets status not_started_here."""
        target = renames.find(live_sessions(), name)
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

    @tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True))
    def watch_session(
        session: Annotated[str, Field(description=SESSION_PARAM)],
        watch: Annotated[
            bool, Field(description="False takes the session out of the news; touching it again puts it back.")
        ] = True,
    ) -> dict[str, Any]:
        """Put a session in the news line on every result, or take it out.
        For a session you haven't touched, such as one the owner hands you, or to drop one. A newly
        watched session starts with nothing new."""
        if not watch:
            name = unwatch(session)
            if name is None:
                raise ToolError(f"{session!r} isn't watched; the news line names the watched sessions")
            store.set_setting(WATCHED_SETTING, watched)
            return {"session": name, "watched": False}
        d = find_live(session)
        watch_live(d)
        store.set_setting(WATCHED_SETTING, watched)
        return {"session": d.get("name"), "watched": True}

    def under_root(cwd: str | None) -> str | None:
        """Folder name: relative to the root, the full path in a granted folder, or None
        if the folder is not open to the bridge."""
        if not cwd:
            return None
        try:
            path = manager.resolve_folder(cwd)
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
        return renamed(events.read_live(live_dir))

    @tool(annotations=READ_ONLY)
    def list_active_sessions() -> dict[str, Any]:
        """Every session running now, with status; a waiting one carries its prompt.
        Claude Code sessions on this machine: status busy, idle or waiting (prompt says what it
        asks); kind "desktop", "terminal" or "background" (no window); side_chat marks a desktop
        aside. A session outside the folders open to the bridge (the root and what's under it, and
        those opened with request_access; open in an app doesn't count) shows only its name, folder
        and status, with access "folder_not_open" (request_access opens it once the owner confirms)
        or "folder_closed" (inside or holding a secret or system folder; never opened)."""
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
                        "kind": session_kind(d),
                        "access": f"folder_{manager.access.state(str(d.get('cwd')))}",
                        "minutes_since_update": round((now_ms - d.get("updatedAt", now_ms)) / 60000),
                    }
                )
                continue
            row = {
                "claude_session_id": d.get("sessionId"),
                "name": d.get("name"),
                "folder": name,
                "status": d.get("status"),
                "kind": session_kind(d),
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
    def list_claude_conversations(
        folder: Annotated[
            str | None, Field(description="Only conversations in this folder, by its name in list_folders.")
        ] = None,
        limit: Annotated[int, Field(description="How many conversations, at most 50.")] = 10,
    ) -> dict[str, Any]:
        """Earlier conversations, newest first; resume one with attach_conversation.
        Recent Claude Code conversations on this machine, including ones started in a terminal."""
        directory = None
        if folder:
            try:
                directory = str(manager.resolve_folder(folder))
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
                    "folder": name,
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
        claude_session_id: Annotated[str, Field(description="The conversation's id, from list_claude_conversations.")],
        folder: Annotated[str | None, Field(description="Its folder; left out, the folder it ran in.")] = None,
        label: Annotated[str | None, Field(description="The name it runs under; left out, its title.")] = None,
    ) -> dict[str, Any]:
        """Resume an earlier conversation on Remote Control; then talk to it.
        Continues it like start_active_session, in its own permission mode and folders. If Claude
        Code can't continue it in place, as when it is still open elsewhere, it starts a copy under
        a new id, and copied_from names the original: say so; a claude_code_note is Claude Code's
        own remark on the start."""
        if folder is None:
            live = next((d for d in live_sessions() if d.get("sessionId") == claude_session_id), None)
            match = None
            if live is None:
                match = next(
                    (c for c in conversations(limit=1000) if c.session_id == claude_session_id),
                    None,
                )
                if match is None:
                    raise ToolError(f"No Claude Code conversation with id {claude_session_id}")
            folder = (live or {}).get("cwd") or (match.cwd if match else "") or ""
            label = label or (live or {}).get("name") or (match.custom_title or match.summary if match else None)
        try:
            path = manager.resolve_folder(folder)
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
        found = renames.find([d for d in live_sessions() if not is_courier(d)], session)
        if found is not None:
            return found
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

    def folder_to_open(session: str | None, folder: str | None) -> tuple[str, dict[str, Any]]:
        if (session is None) == (folder is None):
            raise ToolError("Give one of session or folder.")
        if folder is not None:
            if not Path(folder).is_absolute():
                raise ToolError(f"{folder!r}: give the folder's full path.")
            return folder, {}
        d = find_any_live(str(session))
        return str(d.get("cwd")), d

    def open_folder(target: str, by_path: bool, session_name: str | None) -> dict[str, Any]:
        """Open a folder the owner confirmed with a passkey; a folder by path is trusted too."""
        try:
            opened = manager.access.grant(target)
        except ValueError as exc:
            return {"folder": target, "granted": False, "error": str(exc)}
        out: dict[str, Any] = {"folder": str(opened), "granted": True}
        if session_name:
            out["session"] = session_name
        if by_path:
            # A session already runs in a session's folder, so Claude Code trusts it; a folder by
            # path may never have been, and the owner's confirmation covers its trust too.
            root = access.trust_root(opened)
            if manager.access.state(root) == "closed":
                out["trust_not_set"] = f"Claude Code's trust is kept on {root}: {manager.access.explain(root)}"
                return out
            try:
                out["trusted"] = access.trust_in_claude_code(opened, claude_config)
            except (OSError, ValueError) as exc:
                out["trust_not_set"] = f"Claude Code's workspace trust could not be saved: {exc}"
        return out

    def link_to(req: dict[str, Any]) -> str:
        return f"{page_url}/confirm/r/{req['link']}"

    @tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    def request_access(
        session: Annotated[str | None, Field(description=SESSION_PARAM)] = None,
        folder: Annotated[
            str | None,
            Field(description=OPEN_FOLDER_PARAM),
        ] = None,
    ) -> dict[str, Any]:
        """Open a folder to the bridge, once the owner confirms with a passkey.
        Give `session`, to open the folder it runs in, or `folder`. It stays open, with everything
        under it, until revoke_access takes it back. Send the owner
        the returned link in the chat: the page shows the code and the folder, and they confirm
        there with their passkey."""
        target, d = folder_to_open(session, folder)
        resolved = Path(target).resolve()
        if manager.access.state(resolved) == "closed":
            raise ToolError(manager.access.explain(resolved))
        if not resolved.is_dir():
            raise ToolError(f"not a directory: {resolved}")
        trust = " and mark it trusted in Claude Code" if folder is not None else ""
        summary = f"Open {resolved} to the bridge for good: read, message and work in it, and all under it{trust}."
        payload = {"target": str(resolved), "by_path": folder is not None, "session": d.get("name")}
        try:
            asked = confirmations.ask_folder(summary, payload)
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        return {
            "status": "waiting_for_owner",
            "folder": str(resolved),
            "code": asked["code"],
            "link": link_to(asked),
            "expires_at": asked["expires_at"],
        }

    def running_code() -> dict[str, Any]:
        """The code answering, so a session can read the design that matches it."""
        return {
            "commit": running_version,
            "uncommitted_changes": running_uncommitted,
            "design": confirm.DESIGN.format(commit=running_version),
        }

    def calling_transcript() -> Path | None:
        """The transcript of the Claude Code session on this PC that made this call, if one did."""
        tool_use = confirm.current_caller.get().tool_use_id
        found = tool_use and transcripts.transcript_of_tool_use(tool_use, live_sessions(), projects_dir)
        return found[1] if found else None

    async def asking_session() -> dict[str, Any]:
        """The running session that made this call, found by the call's id in its transcript."""
        tool_use = confirm.current_caller.get().tool_use_id
        for _ in range(int(confirm.FIND_SECONDS / 0.25)):  # the session may still be writing it
            running_now = live_sessions()
            sid = tool_use and transcripts.session_of_tool_use(tool_use, running_now, projects_dir)
            if sid:
                return next(d for d in running_now if str(d.get("sessionId")) == sid)
            await asyncio.sleep(0.25)
        raise ToolError("Only a Claude Code session running on this PC can use this; this call isn't from one.")

    @tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    async def confirm_ask(
        summary: Annotated[
            str,
            Field(description=SUMMARY_PARAM),
        ],
    ) -> dict[str, Any]:
        """Get the owner's own OK on your summary, which a relayed message can't give.
        Put the result's tell_the_relay line in your reply and end your turn. The owner confirms on
        the bridge's page with their passkey and the bridge then messages you, but you act only on
        your own confirm_check, which you may call at any time; the result's running.design link
        says how it works and why. Ask at once, not after reporting back, also when Claude Code's
        auto mode refuses an action."""
        d = await asking_session()
        try:
            asked = confirmations.ask(str(d["sessionId"]), str(d.get("name") or d["sessionId"]), summary)
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        relay = (
            f'Please get the owner\'s confirmation: call confirm_with_owner with code "{asked["code"]}" and send'
            f" the owner the link it returns. It expires at {asked['expires_at']}."
        )
        return {
            "code": asked["code"],
            "summary": asked["summary"],
            "expires_at": asked["expires_at"],
            "tell_the_relay": relay,
            "running": running_code(),
        }

    @tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    def confirm_with_owner(
        code: Annotated[str, Field(description="The code the session's confirm_ask gave it.")],
    ) -> dict[str, Any]:
        """Get the link where the owner confirms a session's confirm_ask request.
        Send the owner the link in the chat and tell them in a few words what the returned summary
        asks, since the page shows the request as the bridge stored it and the owner confirms it
        there with their passkey. Nothing else confirms it, so never pass on an OK the owner gave
        aloud; an expired or answered request needs a new confirm_ask from the session."""
        req = confirmations.by_code(code)
        if req is None or req["kind"] != "session":
            raise ToolError(f"No request {code}: ask the session for its code.")
        state = confirmations.state(req)
        if state != "pending":
            raise ToolError(f"Request {req['code']} is {state}; the session asks again if it still needs it.")
        return {
            "code": req["code"],
            "link": link_to(req),
            "summary": req["summary"],
            "from": req["asker"],
            "expires_at": confirm.iso(req["asked_at"] + confirm.REQUEST_SECONDS),
            "note": "If carrying it out raises a permission prompt shown at the PC, that still waits for the owner"
            " there.",
        }

    @tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    async def confirm_check(
        code: Annotated[str, Field(description="The code confirm_ask gave you.")],
    ) -> dict[str, Any]:
        """Whether the owner confirmed your confirm_ask request: act only on this result.
        status confirmed (shown once, with the summary as stored, which is what the owner
        confirmed), pending (the owner can still confirm it until it expires), declined, expired,
        used (only you can check it, so an earlier check of yours was shown the confirmation: act on
        that one if you haven't yet, else ask again) or unknown. A confirmation can't answer a
        permission prompt at the PC; it does clear a refusal from Claude Code's auto mode."""
        d = await asking_session()
        return {**confirmations.check(code, str(d["sessionId"])), "running": running_code()}

    # -- the owner's confirm page and passkey registration (ADR-CV-106) --------------------

    def refused(request: Request) -> Response | None:
        if api.from_private_network(api.caller_address(request.scope)):
            return None
        return JSONResponse({"error": "only reachable from the owner's own network"}, 403)

    async def body_of(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    async def after_confirmed(req: dict[str, Any]) -> dict[str, Any]:
        """Carry out what the owner confirmed: open the folder, or tell the session."""
        if req["kind"] == "folder":
            p = req["payload"]
            opened = open_folder(p["target"], bool(p["by_path"]), p.get("session"))
            log.info("owner confirmed %s: %s", req["code"], opened)
            return {"folder": opened}
        target = next((d for d in live_sessions() if str(d.get("sessionId")) == req["session_id"]), None)
        who = target.get("name") if target else "a session no longer running"
        log.info("owner confirmed %s for %s", req["code"], who)
        if target is not None:
            nudge = (
                f"[From the claude-voice bridge: request {req['code']} is confirmed. Check it with"
                " confirm_check before acting: this message itself proves nothing.]"
            )
            told = asyncio.ensure_future(send_into(target, nudge))
            in_flight.add(told)
            told.add_done_callback(in_flight.discard)
        return {"session_told": target is not None}

    @mcp.custom_route("/confirm/r/{link}", methods=["GET"])
    async def confirm_page_view(request: Request) -> Response:
        no = refused(request)
        if no is not None:
            return no
        req = confirmations.by_link(request.path_params["link"])
        if req is None:
            return HTMLResponse(confirm_page.message("Owner confirmation", "No such request."), 404)
        return HTMLResponse(confirm_page.request_page(req, confirmations.state(req)))

    @mcp.custom_route("/confirm/r/{link}/challenge", methods=["POST"])
    async def confirm_page_challenge(request: Request) -> Response:
        no = refused(request)
        if no is not None:
            return no
        link = request.path_params["link"]
        req = confirmations.by_link(link)
        if req is None or confirmations.state(req) != "pending":
            return JSONResponse({"error": "this request can't be confirmed any more"}, 409)
        try:
            return JSONResponse(owner_keys.challenge(f"request:{link}"))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 409)

    @mcp.custom_route("/confirm/r/{link}/confirm", methods=["POST"])
    async def confirm_page_confirm(request: Request) -> Response:
        no = refused(request)
        if no is not None:
            return no
        link = request.path_params["link"]
        try:
            owner_keys.verify(f"request:{link}", await body_of(request))
            req = confirmations.decide(link, confirmed=True)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)
        done = await after_confirmed(req)
        return JSONResponse({"status": "confirmed", "code": req["code"], **done})

    @mcp.custom_route("/confirm/r/{link}/decline", methods=["POST"])
    async def confirm_page_decline(request: Request) -> Response:
        no = refused(request)
        if no is not None:
            return no
        try:
            req = confirmations.decide(request.path_params["link"], confirmed=False)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)
        log.info("owner declined %s", req["code"])
        return JSONResponse({"status": "declined", "code": req["code"]})

    @mcp.custom_route("/confirm/passkey/{token}", methods=["GET"])
    async def passkey_page(request: Request) -> Response:
        no = refused(request)
        if no is not None:
            return no
        try:
            owner_keys.check_registration(request.path_params["token"])
        except ValueError as exc:
            return HTMLResponse(confirm_page.message("Add a passkey", str(exc)), 410)
        return HTMLResponse(confirm_page.registration_page())

    @mcp.custom_route("/confirm/passkey/{token}/options", methods=["POST"])
    async def passkey_options(request: Request) -> Response:
        no = refused(request)
        if no is not None:
            return no
        try:
            return JSONResponse(owner_keys.registration_options(request.path_params["token"]))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 410)

    @mcp.custom_route("/confirm/passkey/{token}/register", methods=["POST"])
    async def passkey_register(request: Request) -> Response:
        no = refused(request)
        if no is not None:
            return no
        body = await body_of(request)
        try:
            owner_keys.register(request.path_params["token"], body.get("credential") or {}, str(body.get("name") or ""))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)
        log.info("a passkey was added: %s", body.get("name"))
        return JSONResponse({"status": "added"})

    @tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    def revoke_access(
        folder: Annotated[str, Field(description="A folder opened with request_access, as health lists it.")],
    ) -> dict[str, Any]:
        """Take back a folder opened with request_access: it is folder_not_open again."""
        try:
            closed = manager.access.revoke(folder)
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        return {"folder": str(closed), "granted": False}

    def address(d: dict[str, Any]) -> str:
        """The name Claude Code itself answers to, which a rename may not have reached yet."""
        return str(d.get("deliver_to") or d["name"])

    async def send_into(target: dict[str, Any], text: str) -> dict[str, Any]:
        """Write `text` into a running session: through its channel when it has one, else by courier.

        A message the channel took but never confirmed is not carried again by the courier: it may
        already be in the session, and twice is worse than a failure the relay hears about."""
        if channels is not None:
            # RULE: a session with a connected channel gets the message through it, not the courier
            out = await channels.deliver(str(target.get("sessionId")), text)
            if out is not None:
                log.info(
                    "channel -> %s %s", target.get("name"), "delivered" if out.get("delivered") else "not delivered"
                )
                return out
        return {**await deliver(address(target), text), "path": "courier"}

    @tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    async def rename_session(
        session: Annotated[str, Field(description=SESSION_PARAM)],
        new_name: Annotated[str, Field(description="Any text on one line, up to 64 characters.")],
    ) -> dict[str, Any]:
        """Give a running session a new name; its old name still reaches it.
        A session the bridge started is renamed in Claude Code too when it is idle, and the phone
        and the desktop app show it within seconds; for any other, only the bridge's lists know the
        new name. claude_code: renamed, busy (mid-turn: renamed in the bridge only; rename it again
        once idle), bridge_only, or already (Claude Code had that name); note says more."""
        new_name = renames.clean(new_name)
        if not renames.valid(new_name):
            raise ToolError("A session name needs at least one visible character.")
        rows = [d for d in live_sessions() if under_root(d.get("cwd"))]
        target = renames.find(rows, by_number(session))
        if target is None:
            raise ToolError(f"No running session called {session!r}; see list_active_sessions")
        sid, old = str(target["sessionId"]), str(target["name"])
        # RULE: a new name already in use is refused
        if new_name in renames.taken(rows, but=sid):
            raise ToolError(f"{new_name!r} is already in use by another session")
        claude_name = address(target)
        manager.store.rename(sid, claude_name, new_name, [old, claude_name, *target.get("aliases", [])])
        claude_code, note = rename_in_claude(target, claude_name, new_name)
        log.info("renamed %s -> %s (%s); Claude Code: %s", old, new_name, sid, claude_code)
        return {"name": new_name, "was": old, "claude_session_id": sid, "claude_code": claude_code, "note": note}

    def rename_in_claude(d: dict[str, Any], claude_name: str, new: str) -> tuple[str, str]:
        """Write the new name into Claude Code's job record for a session the bridge started,
        only while it waits idle. Claude Code renames a running background session from it at once,
        and a wake reads it, so the name stays. Any other session keeps Claude Code's own name."""
        if claude_name == new:
            return "already", "Claude Code already has this name."
        sid = str(d.get("sessionId"))
        # RULE: only a session the bridge started is renamed in Claude Code itself
        if store.started_session(sid) is None:
            return "bridge_only", (
                f"The new name is the bridge's only: the bridge didn't start this session, so Claude Code"
                f" and the desktop app keep the name {claude_name}. Type /rename {new} in it to rename it"
                " there too; messages to either name reach it."
            )
        # RULE: the job record of a busy session is never written
        if d.get("status") != "idle":
            return "busy", "Renamed in the bridge; Claude Code is mid-turn, so rename it again once it is idle."
        try:
            transcripts.rename_job(sid, new, jobs_dir)
        except OSError as exc:
            return "bridge_only", (
                f"The new name is the bridge's only: Claude Code's job record for it couldn't be written ({exc}),"
                f" so Claude Code and the desktop app keep the name {claude_name}."
            )
        return "renamed", "Renamed in Claude Code too; the session lists show it within seconds."

    @tool(
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
    )
    async def message_active_session(
        session: Annotated[str, Field(description=SESSION_PARAM)],
        message: Annotated[str, Field(description="What to send; the session answers you, the relay.")],
    ) -> dict[str, Any]:
        """Send to a session and return at once; wait_for_session then gets the reply.
        Returns once delivered, in a few seconds; use it when your next step doesn't depend on the
        answer, then wait_for_session(after=next_after), or read_session_output(after=next_after)
        later."""
        target = find_live(session)
        moved = moved_to(target)
        if moved:
            return {"session": target["name"], "delivered": False, "status": "moved", "moved_to": moved}
        sid = str(target["sessionId"])
        before = len(numbered_turns(target))
        budget = repos.budgets.seconds(transcripts.current_client.get())
        delivery = asyncio.ensure_future(send_into(target, f"{message}\n\n{RELAYED_MESSAGE_NOTE}"))
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

    def reply_is_final(status: str, new: list[dict[str, Any]]) -> bool:
        """Whether a reply is the session's last word on it."""
        # RULE: a reply is final only once the turn has ended
        return bool(new) and status in ("answered", "idle", "needs_input", "needs_choice")

    def live_by_pid(pid: Any) -> dict[str, Any] | None:
        """The running session with this process id now, or None once it has exited."""
        return next((d for d in live_sessions() if d.get("pid") == pid), None)

    def reply_of(turns: list[dict[str, Any]], max_chars: int, final: bool) -> dict[str, Any]:
        """The reply, the last message: whole up to FINAL_CHARS once the turn has ended, with
        reply_cut and reply_length past that, else cut to max_chars. Each message in `turns` is
        cut to max_chars, so a long report is sent once, in reply, not twice."""
        if not turns:
            return {"reply": ""}
        last = turns[-1]["text"]
        out: dict[str, Any] = {"reply": transcripts.clip(last, FINAL_CHARS if final else max(max_chars, 20))}
        if final and len(last) > FINAL_CHARS:
            out.update(reply_cut=True, reply_length=len(last))
        for turn in turns:
            turn["text"] = transcripts.clip(turn["text"], max(max_chars, 20))
        return out

    async def await_reply(
        target: dict[str, Any], before: int, was_busy: bool, probe: str, wait_seconds: float
    ) -> tuple[dict[str, Any], str, dict[str, Any] | None]:
        """Watch a session we just sent a message until it settles, ends or the wait runs out.

        Gives back the session as last seen, the status, and what it waits for, if anything."""
        pid, seen_busy = target.get("pid"), False
        loop = asyncio.get_running_loop()
        started = loop.time()
        quiet_since: float | None = None
        deadline = started + within_budget(wait_seconds)
        while True:
            now = live_by_pid(pid)
            if now is None:
                return target, "session_ended", None
            target = now
            # A quick turn can start and end between two looks, so a new reply
            # counts as much as having seen the session busy.
            seen_busy = seen_busy or now.get("status") in BUSY
            new_turns = numbered_turns(now)[before:]
            received_late = loop.time() - started >= receive_seconds
            settled = _settled(now, new_turns, probe, was_busy, seen_busy, received_late)
            if settled == "answered" and not _reply_turns(new_turns, probe, before, "idle", was_busy)[0]:
                # Idle with no answer to read yet: its last message may still be on its way.
                quiet_since = quiet_since if quiet_since is not None else loop.time()
                settled = settled if loop.time() - quiet_since >= silent_grace else None
            else:
                quiet_since = None
            if settled == "waiting":
                blocked = waiting(now)
                return target, (blocked["status"] if blocked else "needs_input"), blocked
            if settled:
                return target, settled, None
            if loop.time() >= deadline:
                return target, "still_working", None
            await asyncio.sleep(poll_seconds)

    @tool(annotations=READ_ONLY)
    async def wait_for_session(
        session: Annotated[str, Field(description=SESSION_PARAM)],
        wait_seconds: Annotated[float, Field(description=WAIT_PARAM)] = WAIT_SECONDS,
        after: Annotated[
            int | None,
            Field(description=WAIT_AFTER_PARAM),
        ] = None,
        max_chars: Annotated[int, Field(description=MAX_CHARS_PARAM)] = 600,
    ) -> dict[str, Any]:
        """Wait for a session's turn to end or need input, up to 20 s; then its reply.
        Sends nothing; reply is the newest message among those returned, empty when the session
        wrote nothing new; at most 30 turns come back, and read_session_output(after=next_after)
        reads the rest while turns_left is above 0. status: "idle" (its turn
        has ended), "needs_input" or "needs_choice" (it waits for its user; prompt or question says
        what for), "still_working" (the wait ran out; call again), "session_ended" or "moved" (the
        conversation now lives in moved_to); reply_final: its turn ended with something new, and
        reply is whole up to 12,000 characters (reply_cut past that)."""
        target = find_live(session)
        moved = moved_to(target)
        if moved:
            return {"session": target["name"], "status": "moved", "moved_to": moved, "session_ended": False}
        # Without `after`, the wait starts where the relay last read this session, so it never skips
        # anything unread; a session never read starts where its transcript ends now.
        mark = watched.get(str(target.get("sessionId")))
        if after is not None:
            start = after
        elif mark is not None:
            start = int(mark["seen"])
        else:
            start = len(numbered_turns(target)) - 1

        def past(d: dict[str, Any], role: str) -> list[dict[str, Any]]:
            """The session's turns in this role after `start`."""
            return [t for t in numbered_turns(d) if t["role"] == role and t["index"] > start]

        pid, blocked, done, taken = target.get("pid"), None, False, False
        clock = asyncio.get_running_loop().time
        deadline = clock() + within_budget(wait_seconds)
        quiet_since: float | None = None
        while (now := live_by_pid(pid)) is not None:
            target, blocked = now, waiting(now)
            idle = now.get("status") == "idle"
            # A message is in the transcript and the session idle on two looks running, or after
            # it was seen busy: its turn has ended, even if it wrote nothing. Claude Code may mark
            # it idle a moment before its last message can be read, so a turn that seems to have
            # ended with nothing new is believed only once it has looked that way for a while.
            quiet = idle and taken and bool(past(now, "user"))
            quiet_since = (quiet_since if quiet_since is not None else clock()) if quiet else None
            # RULE: a turn that seems to end with nothing new is believed only after a short while
            ended_silent = quiet_since is not None and clock() - quiet_since >= silent_grace
            taken = not idle or bool(past(now, "user"))
            done = blocked is not None or (idle and (after is None or bool(past(now, "assistant")) or ended_silent))
            if done or asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(poll_seconds)
        if now is None:
            status = "session_ended"
        else:
            status = blocked["status"] if blocked else ("idle" if done else "still_working")
        # A session that ended keeps its transcript, so what it wrote before it went is still read.
        written = past(target, "assistant")
        final = reply_is_final(status, written)
        reply = reply_of([dict(t) for t in written[-1:]], max_chars, final)
        new = [dict(t) for t in written[:30]]
        reply_of(new, max_chars, False)  # cuts each message in turns to max_chars
        left = len(written) - len(new)
        latest = {"index": written[-1]["index"], "role": "assistant", "text": reply["reply"]} if left else None
        return {
            "status": status,
            "session": target["name"],
            **reply,
            "reply_final": final,
            "session_ended": now is None,
            "turns": new,
            "turns_left": left,
            **({"latest": latest} if latest else {}),
            "next_after": new[-1]["index"] if new else start,
            **({"question": blocked["question"]} if status == "needs_choice" and blocked else {}),
        }

    @tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    async def ask_active_session(
        session: Annotated[str, Field(description=SESSION_PARAM)],
        message: Annotated[str, Field(description="What to ask; the session answers you, the relay.")],
        wait_seconds: Annotated[float, Field(description=WAIT_PARAM)] = WAIT_SECONDS,
        max_chars: Annotated[int, Field(description=MAX_CHARS_PARAM)] = 600,
    ) -> dict[str, Any]:
        """Ask a session and wait, up to 20 s, for its answer; only when needed now.
        reply_final: its turn ended with an answer, whole up to 12,000 characters (reply_cut past
        that). status: "answered", "needs_input" (it waits for its user; prompt is the permission
        prompt, if one; before sending, nothing was sent), "still_working" (the wait ran out before
        its turn ended; continue with wait_for_session(after=next_after)), "session_ended" (it
        exited meanwhile), "not_received" (the message never reached its conversation), "moved"
        (the conversation now lives in moved_to; nothing sent), or "needs_choice" (it waits for its
        user to pick one of question.options)."""
        target = find_live(session)
        moved = moved_to(target)
        if moved:
            return _not_sent(target, {"status": "moved", "moved_to": moved})
        blocked = waiting(target)
        if blocked:
            # A message would only queue behind what it waits for; send nothing.
            return _not_sent(target, blocked)
        before = len(numbered_turns(target))
        was_busy = target.get("status") in BUSY
        out = await send_into(target, f"{message}\n\n{RELAYED_MESSAGE_NOTE}")
        if not out.get("delivered"):
            raise ToolError(f"Could not deliver: {out.get('detail') or 'unknown reason'}")

        probe = message.strip()[:80]
        target, status, blocked = await await_reply(target, before, was_busy, probe, wait_seconds)
        fresh = numbered_turns(target)[before:] if status != "session_ended" else []
        new, start = _reply_turns(fresh, probe, before, status, was_busy)
        final = reply_is_final(status, new)
        reply = reply_of(new, max_chars, final)
        return {
            "session": target["name"],
            "status": status,
            "session_ended": status == "session_ended",
            **reply,
            "reply_final": final,
            "turns": new,
            "next_after": new[-1]["index"] if new else start,
            **({"question": blocked["question"]} if status == "needs_choice" and blocked else {}),
        }

    @tool(annotations=READ_ONLY)
    def read_session_output(
        session: Annotated[str, Field(description=SESSION_PARAM)],
        limit: Annotated[
            int | None, Field(description="How many turns; left out, as many as fit in about 3,000 characters.")
        ] = None,
        max_chars: Annotated[int, Field(description=MAX_CHARS_PARAM)] = 600,
        after: Annotated[
            int | None, Field(description="The next_after a send or a read returned: the turns after it, oldest first.")
        ] = None,
        turn: Annotated[
            int | None,
            Field(
                description=(
                    "A turn's index, as a read numbers its turns (from 0), to read that one turn whole, "
                    "page by page with offset."
                )
            ),
        ] = None,
        offset: Annotated[
            int, Field(description="Where to start in that turn: the next_offset the last page returned.")
        ] = 0,
    ) -> dict[str, Any]:
        """A session's latest turns, or those after `after`, until turns_left is 0.
        Turns are numbered: pass next_after back as `after` for the next ones, with turns_left
        saying how many more follow (read on until it is 0; when far behind, `latest` holds its
        newest reply). The first turn that doesn't fit is still included, cut. A cut turn says so (cut,
        length); a finished turn's last message is cut only past 12,000 characters."""
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

        # The last message of a finished turn comes back whole, up to FINAL_CHARS.
        final_index = (
            turns[-1]["index"]
            if turns and turns[-1]["role"] == "assistant" and target.get("status") == "idle"
            else None
        )
        left = 0
        if after is None:
            picked = taken(turns[::-1], forward=False)[::-1]
        else:
            later = [t for t in turns if t["index"] > after]
            picked = taken(later, forward=True)
            left = len(later) - len(picked)  # read on with next_after until this is 0
        turns = []
        for t, room in picked:
            if t["index"] == final_index:
                room = FINAL_CHARS
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
    async def peek_session(
        session: Annotated[str, Field(description=SESSION_PARAM)],
        question: Annotated[str, Field(description="What to ask about its last turns; it never enters the session.")],
        language: Annotated[str | None, Field(description=LANGUAGE_PARAM)] = None,
    ) -> dict[str, Any]:
        """A small model answers about a session's last turns in 3-7 s; it reads no code.
        A small separate model with no tools answers from the last 8,000 characters of the
        conversation, a few turns of words and tool names, never tool results or files. For the
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
    async def ask_forked_session(
        session: Annotated[str, Field(description=SESSION_PARAM)],
        question: Annotated[
            str, Field(description="What to ask; each question forks the session anew, from its latest state.")
        ],
        wait_seconds: Annotated[float, Field(description=WAIT_PARAM)] = WAIT_SECONDS,
    ) -> dict[str, Any]:
        """Ask the side session, a read-only fork that reads code; waits up to 20 s.
        The fork can read the files in the session's folder; the session keeps working, untouched.
        status "answered", "still_working" (read it later with read_forked_session(fork_id)),
        "timed_out", or "error" with `detail`."""
        return await collect_fork(start_fork(session, question), wait_seconds)

    @tool(annotations=READ_ONLY)
    async def message_forked_session(
        session: Annotated[str, Field(description=SESSION_PARAM)],
        question: Annotated[
            str, Field(description="What to ask; each question forks the session anew, from its latest state.")
        ],
    ) -> dict[str, Any]:
        """Send the side session a question and return; read it with read_forked_session.
        Like ask_forked_session, but returns at once; read_forked_session(fork_id) gets the
        answer."""
        fork_id = start_fork(session, question)
        return {"fork_id": fork_id, "session": forks[fork_id]["session"], "status": "started"}

    @tool(annotations=READ_ONLY)
    async def read_forked_session(
        fork_id: Annotated[
            str, Field(description="The fork_id ask_forked_session or message_forked_session returned.")
        ],
        wait_seconds: Annotated[float, Field(description=WAIT_PARAM)] = WAIT_SECONDS,
    ) -> dict[str, Any]:
        """The side session's answer, waiting up to 20 s; call again on still_working.
        status "answered", "still_working" (call again), "timed_out" (it worked five minutes and
        was stopped) or "error" with `detail`; answers are kept for an hour, and forgotten when
        the bridge restarts."""
        return await collect_fork(fork_id, wait_seconds)

    @tool(annotations=READ_ONLY)
    async def digest_session(
        session: Annotated[str, Field(description=SESSION_PARAM)],
        question: Annotated[str | None, Field(description="A question to answer instead of a summary.")] = None,
        language: Annotated[str | None, Field(description=LANGUAGE_PARAM)] = None,
    ) -> dict[str, Any]:
        """Sum up a session's whole conversation, or answer a question on it, in ~10 s.
        Like peek_session over up to the last 150,000 characters, so slower: a few speakable
        sentences; none of it fills your context. cut and skipped_chars say when the start of the
        conversation was left out: say so."""
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
    def search_session_history(
        session: Annotated[str, Field(description=SESSION_PARAM)],
        query: Annotated[str, Field(description="Keywords to look for.")],
        max_chars: Annotated[int, Field(description=MAX_CHARS_PARAM)] = 300,
    ) -> dict[str, Any]:
        """Search a session's whole conversation for keywords: only relevant excerpts.
        Use it instead of reading long output when looking for something said earlier."""
        target = find_live(session)
        turns = transcripts.recent_turns(read_transcript(target["sessionId"], target.get("cwd")), None)
        size = max(max_chars, 40)
        matches = transcripts.search_turns(turns, query, limit=5, context_chars=size // 2 - 10)
        for match in matches:
            match["text"] = transcripts.clip(match["text"], size)
        return {"session": target["name"], "total_turns": len(turns), "matches": matches}

    def event_feed(cursor: str | None = None) -> dict[str, Any]:
        """The watcher's events since `cursor`, and the cursor to pass next time; for the
        companion API's event stream, no longer a tool since every result carries the news."""
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
        when the shape changes."""
        return tree.build()

    @tool(annotations=READ_ONLY)
    def health() -> dict[str, Any]:
        """The bridge's own state: version, uptime, calls and errors per tool, folders.
        Since when and which version, memory, the last restart, calls/errors/latency per tool
        since start, the latest errors, courier, channels, watcher, and the root and granted folders."""
        snap = METRICS.snapshot()
        courier = transcripts.shared_courier()
        offered = [p.status() for p in active_plugins]
        return {
            "plugins": offered,
            "developer_mode": developer_mode.state(),
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
            "channels": channels.status() if channels is not None else None,
            "watcher": {
                "last_check_at": last_check[0] if last_check else None,
                "tree_version": tree_version[0] if tree_version else tree.build()["version"],
            },
            "access": {"root": str(manager.root), "granted": [str(p) for p in manager.access.granted()]},
        }

    def caller() -> str:
        return transcripts.current_client.get()

    @tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    async def plugin_restart(name: str) -> dict[str, Any]:
        """Start one plugin again so its new code is used, without restarting the bridge.
        Its tools come back only if they still take what the owner approved. Read 'say' aloud."""
        return await desk.restart(name)

    @tool(annotations=ToolAnnotations(read_only_hint=True))
    def developer_mode_request() -> dict[str, Any]:
        """Ask the owner to switch developer mode on: read 'say' aloud exactly.
        Call developer_mode_confirm with the token only if the owner clearly answers yes.
        Developer mode lets the owner approve changed plugin tools by voice; it ends after 8 hours."""
        return desk.mode_request(caller())

    @tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    def developer_mode_confirm(token: str) -> dict[str, Any]:
        """Switch developer mode on, with the token from developer_mode_request.
        Only after the owner's own clear yes to the question just read."""
        return desk.mode_confirm(caller(), token)

    @tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    def developer_mode_off() -> dict[str, Any]:
        """Switch developer mode off at once. No question is needed."""
        return desk.mode_off(caller())

    @tool(annotations=ToolAnnotations(read_only_hint=True))
    async def plugin_approve_request(name: str) -> dict[str, Any]:
        """Ask the owner to approve a plugin whose tools changed: read 'say' aloud.
        It names each changed tool. Call plugin_approve_confirm with the token only on the
        owner's clear yes. Works only in developer mode."""
        return await desk.approve_request(caller(), name)

    @tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
    async def plugin_approve_confirm(token: str) -> dict[str, Any]:
        """Approve the changed plugin, with the token from plugin_approve_request.
        Only after the owner's own clear yes to the question just read."""
        return await desk.approve_confirm(caller(), token)

    # -- the session the voice talks to, and the companion page that picks it -----------

    def resolve_session(session: str) -> dict[str, Any]:
        """A running session or one of the bridge's own, by name or id, as the page shows it."""
        session = by_number(session)
        for row in fleet_recap()["sessions"]:
            if session in (row["id"], row["name"]):
                return {"id": row["id"], "number": row.get("number"), "name": row["name"], "kind": row["kind"]}
        raise LookupError(f"No session called {session!r}")

    def set_active(session: str, by: str) -> dict[str, Any]:
        found = across.active_row(session) if across.split(session) else resolve_session(session)
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
        found = renames.find([d for d in live_sessions() if under_root(d.get("cwd"))], session)
        if found is not None:
            return found
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
        if across.split(session):
            raise LookupError("summaries are made only for sessions on this computer")
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

    async def _no_prs() -> dict[str, Any]:
        return {"prs": []}  # another machine's pull requests are not looked up from here

    mcp.terminals = terminals or Terminals()  # type: ignore[attr-defined]  # the desktop, swapped in tests

    def focus_session(session_id: str) -> tuple[int, dict[str, str]]:
        """Bring a session's terminal to the front: one on another machine cannot be."""
        if across.split(session_id):
            return NOT_LOCAL
        desk: Terminals = mcp.terminals  # type: ignore[attr-defined]
        return desk.open(session_id, live_sessions(), lambda pid: None)  # no tmux on this fork

    if oauth is not None:

        async def check_token(token: str) -> bool:
            return await oauth.load_access_token(token) is not None

        api.EventApi(
            store,
            oauth.login_secret,
            oauth.public_url,
            check_token,
            sessions=lambda: across.listing(fleet_recap()),
            get_active=lambda: store.get_setting("active_session"),
            set_active=set_active,
            clear_active=clear_active,
            output=lambda s, limit, after: (
                across.output(s, limit, after) if across.split(s) else api_output(s, limit, after)
            ),
            pending=lambda s: across.pending(s) if across.split(s) else api_pending(s),
            prs=lambda s: _no_prs() if across.split(s) else api_prs(s),
            news=event_feed,
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
            work=lambda: {**board.report(), "developer_mode": developer_mode.state()},
            detail=lambda s: across.detail_page(s) if across.split(s) else session_detail(s),
            summary=request_summary,
            update_changes=update_changes_now,
            ui_origins=ui_origins,
            remote_prompts=across.prompts,
            answer_remote=across.answer,
            focus=focus_session,
        ).routes(mcp)

    mcp.fleet = across  # type: ignore[attr-defined]  # the other machines, for tests and the CLI
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
        from starlette.responses import RedirectResponse
        from starlette.routing import Route
        from starlette.staticfiles import StaticFiles

        app.mount("/ui", StaticFiles(directory=str(ui_dir), html=True), name="ui")
        # The plain address is what people type, so it leads to the page rather than "not found".
        app.router.routes.append(Route("/", lambda request: RedirectResponse("/ui/"), methods=["GET"]))
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
        grant_for=session_modes.session_grant,
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
        manager,
        oauth=oauth,
        watch_interval=5.0,
        restart=restart,
        ui_origins=cfg.ui_origins,
        bridge_port=cfg.port,
        bridge_host=cfg.host,
        channels=ChannelHub(
            channel.LINK,
            session_of=lambda pid: ancestor_session(
                pid, lambda: events.read_live(LIVE_DIR), accepts=started_with_channel
            ),
        ),
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
