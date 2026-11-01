# claude-voice

Talk to the Claude Code sessions on your own computer from your phone.

You speak to a voice assistant (Claude's mobile app in voice mode is the one
this was proven with). The assistant calls this bridge over the internet,
and the bridge does the work on your computer:
- it starts and follows Claude Code sessions there;
- it passes your messages into sessions already open in a terminal;
- it reads back what they did.

Claude Code runs under your own Claude subscription, not a pay-per-use API key.
Anything risky, such as editing files or running commands, waits until you say
yes.

The bridge is an [MCP](https://modelcontextprotocol.io) server: a program
offering named tools that an AI assistant can call.

<!-- not run: a diagram, not commands -->
```text
voice assistant on the phone (Claude app, voice mode)
   │  custom connector, signed in with OAuth
   ▼
public HTTPS address (e.g. Tailscale Funnel)
   ▼
claude-voice on your computer
   ├─ sessions it starts ──► Claude Code in the background, on Remote Control
   ├─ side questions ──► Claude Agent SDK ──► a read-only fork of a session
   └─ sessions open elsewhere ──► Claude Code's own session messaging and transcripts
```

## Start here: five minutes on one computer

You need Linux or macOS, a Claude subscription (Pro or Max), and an internet
connection. Nothing else has to be installed first.
An assistant doing this for you should read [AGENTS.md](AGENTS.md) first.

<!-- not run: installs software on the reader's computer; the clean-install job runs it in an empty container on every change -->
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh          # uv, which installs Python tools
curl -fsSL https://claude.ai/install.sh | bash           # Claude Code
export PATH="$HOME/.local/bin:$PATH"
uv tool install git+https://github.com/roykollensvendsen/claude-voice
mkdir -p ~/src                                           # the bridge only works in folders under here
claude-voice check
```

`check` now says that Claude Code is not signed in. Run `claude`, type
`/login`, sign in with your Claude account, and run `claude-voice check` again.
It should print `ready to serve over stdio`. Your projects belong in `~/src`;
to use another folder, set `CLAUDE_VOICE_ROOT` (see [the settings](#settings)).

Then connect it to Claude Code on the same computer:

<!-- not run: changes the reader's Claude Code configuration -->
```bash
claude mcp add --scope user claude-voice -- claude-voice serve
```

Start `claude` in any folder and ask: *"Which Claude Code sessions are running
on this computer?"* The answer comes from the bridge. In Claude Desktop, add
this to its configuration file instead (*Settings → Developer → Edit Config*),
with the full path that `which claude-voice` prints:

<!-- not run: a configuration snippet, not commands -->
```json
{ "mcpServers": { "claude-voice": { "command": "/home/you/.local/bin/claude-voice", "args": ["serve"] } } }
```

That is the whole bridge, used locally. The next two steps let you reach it from
a phone: [run it as a service](#run-it-as-a-service-and-reach-it-from-the-phone),
then give it a public address.

## What you can ask for

| Tool | What it is for |
| --- | --- |
| `guide` | how to use the bridge: its roles, rules and which tool for what; the same text as its server instructions |
| `list_projects` | the folders Claude Code was used in lately, anywhere on the computer, from its own history; or any folder under the root found by a word from its name |
| `whats_new` | only what happened since you last asked: finished, waiting for you, started, ended |
| `fleet_recap` | every session in one call, each with a line on what it is doing |
| `health` | the bridge's own state: uptime, version, memory, calls and errors per tool, courier and watcher |
| `session_tree` | the sessions as a tree, including which ones have been messaging each other |
| `list_active_sessions` | every Claude Code session running on the computer now, terminal or background |
| `request_access`, `revoke_access` | open a listed session's folder to the bridge for good, with your approval in the app; close it again |
| `start_active_session` | start a new Claude Code session in the background, ready to talk to at once, optionally on a chosen model and effort |
| `stop_active_session` | stop such a session again; sessions the bridge did not start are never stopped |
| `hand_to_desktop` | move such a session, once its turn has ended, into the Claude desktop app's sidebar, through a terminal tab on the PC named after it (Windows) |
| `message_active_session` | send a message into a session that is open in a terminal, and return once delivered |
| `ask_active_session` | ask such a session something and wait for its answer, as if talking to it directly |
| `wait_for_session` | wait, sending nothing, until a session's turn ends or it needs input, then read its reply; call again while it is still working |
| `ask_forked_session`, `message_forked_session`, `read_forked_session` | ask the side session, a read-only fork of a busy session's whole conversation that can read the code: ask and wait (at most 20 s), send and return, or read its answer later |
| `peek_session` | a quick look at a busy session: a small model answers from the last few turns it wrote, never sent into it |
| `digest_session` | a whole conversation summed up, or a question about it answered, in a few sentences; slower than a peek |
| `read_session_output`, `search_session_history` | read what such a session said lately, or search everything it said |
| `list_claude_conversations`, `attach_conversation` | continue an earlier conversation as a background session, in its own permission mode |

## How it behaves

- **Every session is an ordinary Claude Code session.** New work and resumed
  conversations run in the background on Remote Control, so you can open them on
  the phone or at the PC, see what happened and take over. They outlive restarts
  of the bridge.
- **Your subscription, not the API.** The bridge refuses to start while
  `ANTHROPIC_API_KEY` is set, because Claude would then be billed per token.
  Set `CLAUDE_VOICE_ALLOW_API_KEY=1` if that is what you want.
- **Your own Claude Code settings apply**, including your permission rules.
  Nothing is pre-approved beyond them. A conversation the bridge resumes keeps
  its own permission mode, and the folders added to it, as recorded by the
  desktop app or in its transcript; one with no recorded mode gets
  `CLAUDE_VOICE_PERMISSION_MODE`. A new session starts as Claude Code starts any.
- **Risky actions wait for your yes.** Anything Claude Code would normally ask
  you about is asked of you, on the phone or in the desktop app, where you answer
  it; the assistant can tell you a session is waiting, but cannot answer for you.
- **A session open in a terminal is never taken over.**
  - Messages are delivered into it, the way one Claude Code session messages
    another.
  - Continuing such a conversation from the bridge makes a separate copy, and
    the two then carry on independently.
  - The bridge never stops a terminal session.
- **Projects are confined** to `CLAUDE_VOICE_ROOT` and the folders you opened.
  - Every running session is listed, but one outside those folders shows only
    its name, folder and status, marked `access: "folder_not_open"`: it cannot be
    read or messaged.
  - `request_access` opens that session's folder and everything under it, for
    good. In the Claude app, set that tool to *ask each time*: the app then asks
    you before every grant, and only you can answer.
  - Some folders are never opened, inside the root or not: `.ssh`, `.gnupg`,
    `.claude`, `.config/claude-voice` and `AppData` in your home folder, and the
    system's own folders. A session there, or in a folder holding one, is listed
    as `access: "folder_closed"`, and a refusal names the closed folder.
  - One exception: a scratch folder the Claude desktop app made for a quick
    session (`…\Claude-Data\…\scratch-workspaces\…\scratch-*`) can be opened,
    one at a time; nothing above it opens.
  - Why, and what it costs: [ADR-CV-006](decisions/ADR-CV-006-folders-outside-the-root-are-opened-by-the-owner.md), [ADR-CV-007](decisions/ADR-CV-007-desktop-scratch-folders-open-and-clearer-access-names.md).

> **Know the trust boundary.** Signing in proves that the caller is *your*
> assistant. It cannot prove that *you* said yes to a particular action. The
> assistant is told to act only on what you asked for, but a misbehaving one
> could send a session work on its own. Keep your Claude Code permission rules
> sensible, and guard the login secret like an SSH key.

## Settings

`check` says whether the bridge could start, without starting it. It also asks
Claude Code whether it is signed in. When something is missing, it says how to
fix it, and when all is well it prints `ready to serve`. `check --json` lists
every check, for an assistant doing the setup:

```console
$ env -u ANTHROPIC_API_KEY -u CLAUDE_VOICE_ALLOW_API_KEY CLAUDE_VOICE_ROOT=no-such-folder claude-voice check
claude-voice: CLAUDE_VOICE_ROOT is not a directory: no-such-folder. Make it with `mkdir -p no-such-folder`, or set CLAUDE_VOICE_ROOT to the folder that holds your projects.
$ env -u CLAUDE_VOICE_ALLOW_API_KEY CLAUDE_VOICE_ROOT=src ANTHROPIC_API_KEY=sk-ant-example claude-voice check
claude-voice: ANTHROPIC_API_KEY is set, so Claude would bill the API instead of your subscription. Unset it, or set CLAUDE_VOICE_ALLOW_API_KEY=1 if that is intended.
```

`serve` runs it. Over HTTP it refuses to start without a login secret:

```console
$ env -u ANTHROPIC_API_KEY -u CLAUDE_VOICE_TOKEN CLAUDE_VOICE_ROOT=src claude-voice serve --transport http
claude-voice: CLAUDE_VOICE_TOKEN must be set to serve over HTTP. Make one with `export CLAUDE_VOICE_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')`.
```

With a secret it listens on `http://127.0.0.1:8811/mcp`:

<!-- not run: it serves until stopped -->
```
export CLAUDE_VOICE_ROOT=~/src
export CLAUDE_VOICE_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
uv run claude-voice serve --transport http
```

`serve --transport stdio` is for a local MCP client on the same computer. It is
also the default.

| Variable | Default | Meaning |
| --- | --- | --- |
| `CLAUDE_VOICE_ROOT` | `~/src` | Claude may only be started in folders under here |
| `CLAUDE_VOICE_TOKEN` | — | required over HTTP, at least 32 characters; it is also the login secret |
| `CLAUDE_VOICE_PUBLIC_URL` | `https://<first public host>` | the address clients reach the bridge at |
| `CLAUDE_VOICE_PUBLIC_HOSTS` | — | comma-separated hostnames the tunnel serves |
| `CLAUDE_VOICE_HOST` / `_PORT` | `127.0.0.1` / `8811` | keep it on this computer and tunnel in |
| `CLAUDE_VOICE_COURIER_NAME` | `Owner via claude-voice` | the sender name a session sees when the voice messages it, e.g. `Roy via stemmen` |
| `CLAUDE_VOICE_PERMISSION_MODE` | `default` | the permission mode for conversations that have none recorded, e.g. `auto` to match the desktop app |
| `CLAUDE_VOICE_DB` | `~/.local/state/claude-voice/bridge.db` | where sessions and events are kept |

### Signing in

`GET /healthz` answers anyone. `/mcp` needs one of two things:
- an **OAuth access token**, which is how phone apps connect;
- the **login secret** itself, sent as `Authorization: Bearer …`, for local tools.

When an app connects, it registers itself and sends you to a sign-in page on
the bridge. You type the login secret there once.
- Each sign-in attempt allows five tries.
- Access tokens last an hour.
- Refresh tokens last 90 days and are replaced each time they are used.
- Tokens are stored only as hashes.

## Run it as a service and reach it from the phone

This step needs Linux with systemd; on macOS, run `claude-voice serve
--transport http` in a terminal for now. The script lives in the repository,
so clone it first:

<!-- not run: installs a system service on the reader's computer -->
```
git clone https://github.com/roykollensvendsen/claude-voice && cd claude-voice
deploy/install.sh
journalctl --user -u claude-voice -f
```

The script installs a user service that starts with your login. It also writes
a fresh login secret to `~/.config/claude-voice/env`, readable only by you.

The phone needs a public HTTPS address. With Tailscale, first enable Funnel for
the machine in the admin console, then:

<!-- not run: changes the reader's Tailscale configuration -->
```
tailscale funnel --bg --https=443 http://127.0.0.1:8811
```

The bridge is then at `https://<machine>.<tailnet>.ts.net/mcp`. Use port 443:
claude.ai could not reach the bridge on port 10000.

In the Claude app, add a custom connector with that address and connect. Then
set the tools to *always allow*, so the app does not ask before each one.
ChatGPT on a Plus plan could not connect to a custom MCP server from Android at
the time of writing.

## Develop

[CONTRIBUTING.md](CONTRIBUTING.md) has the gates and how a change is made. In
short, tests are written first, and run against a scripted stand-in for Claude,
so they need no account.

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).
