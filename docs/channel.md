# Delivering messages through a channel

The relay's messages reach a running session in one of two ways:

- **The channel**, for the sessions the bridge starts. Each such session runs a small MCP server, `src/claude_voice/channel.py`, from the plugin in `plugin/`. The server declares Claude Code's `claude/channel` capability, connects to the bridge's hub on this computer, and turns each message the bridge hands it into a channel event, which starts a turn in an idle session. No model sits in between: the hand-over takes a fraction of a second.
- **The courier**, for every other session (desktop sessions, sessions started by hand) and whenever a session's channel isn't connected: a small model session that sends the message with Claude Code's SendMessage. It takes a few seconds per message.

Results say which way a message went (`path`: `channel` or `courier`).

## How the parts find each other

- The hub (`src/claude_voice/channel_hub.py`) listens on `127.0.0.1`, on a port it picks at each start, and writes that port and a secret made at the same time to `~/.local/state/claude-voice/channel.json`. A channel reads the file, connects, and sends the secret first; a connection without it is refused. A channel reconnects by itself after a bridge restart.
- Which session a channel serves is found from its process: the Claude Code process that started it is one of its parents, and Claude Code's session registry (`~/.claude/sessions/<pid>.json`) names its session.
- A message counts as delivered once the channel confirms it wrote the event to Claude Code. A message the channel took but never confirmed is reported as not delivered and not sent again, since it may already be in the session.

## Setting it up once

Channels are a research preview. `--channels` loads only plugins on the channel allowlist, and an account without an organization sets that allowlist with a managed policy on the computer. On Windows, a user-level policy needs no administrator rights. These steps are typed by the owner, in PowerShell, from the clone's folder (`<clone>` below):

1. Add the clone's plugin folder as a marketplace: `claude plugin marketplace add <clone>\plugin`
2. Install the plugin for the user: `claude plugin install relay-channel@claude-voice --scope user`
3. Disable it for the user, so sessions the owner starts don't load it (the bridge enables it per session): `claude plugin disable relay-channel@claude-voice --scope user`
4. Put it on the channel allowlist: `if (-not (Test-Path 'HKCU:\SOFTWARE\Policies\ClaudeCode')) { New-Item -Path 'HKCU:\SOFTWARE\Policies\ClaudeCode' | Out-Null }; Set-ItemProperty -Path 'HKCU:\SOFTWARE\Policies\ClaudeCode' -Name Settings -Value '{"channelsEnabled": true, "allowedChannelPlugins": [{"marketplace": "claude-voice", "plugin": "relay-channel"}]}'`

The plugin runs in place from the clone, so the clone stays where it was when the marketplace was added. Its server runs with `uv`, which must be on the PATH of the sessions.

The bridge then starts each session with `--settings` enabling `relay-channel@claude-voice` and `--channels plugin:relay-channel@claude-voice`. Claude Code keeps both in the session's job record, so a session woken later keeps its channel.
