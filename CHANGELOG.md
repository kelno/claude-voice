# Changelog

Every release, what changed in it, and why. Dates are the tag's date. The
format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
versions follow [semantic versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added

- An MCP server that starts, follows, resumes and stops Claude Code sessions
  as background sessions on the owner's computer, on the owner's subscription.
- Sign-in with OAuth for phone apps, and the login secret as a bearer token for
  local tools.
- Sessions open elsewhere can be listed, messaged, read and searched.
- The tools for sessions run inside the bridge (`create_session`, `send_task`,
  `session_recap`, `cancel`, `close_session`, `list_sessions` and the approvals)
  are gone: new and resumed work runs as background sessions the owner can open
  on the phone and at the PC, and answers permission prompts there. They start
  in the background on Remote Control unless one start asks for a terminal window.
- A background session waiting on a permission prompt shows what it asks.
- `attach_conversation` resumes an earlier conversation as a background session,
  on Remote Control and in its own permission mode and folders.
- `start_active_session` can choose the model and effort of the session it
  starts; a model Claude Code doesn't know is reported in the first reply.
- Every result can start with a one-line `news` field: the sessions this conversation follows
  that have turns it hasn't read, and any session waiting on a permission prompt.
- `CLAUDE_VOICE_ACCESS_TOKEN_SECONDS` sets how long an access token lasts, for a
  bridge on a computer that sleeps: the Claude app gives a connector up after about
  an hour of failed token refreshes.
- The log is kept in `bridge.log` next to the database as well as on the console,
  with the outcome of every token refresh, so a connector that dropped while
  nobody watched can be diagnosed afterwards.
- `wait_for_session` waits, sending nothing, until a running session's turn ends
  or it needs its user, so a voice assistant can wait for a reply instead of polling.
- `claude-voice check` and `claude-voice serve`, and a user service installer.

### Security

- A plugin's approval now covers every word the voice reads from it: each tool's
  description and the plugin's own instructions. Before, a changed plugin could rewrite
  them and tell the voice to do something without being approved again. After upgrading,
  every plugin is approved once more, typed or by voice in developer mode.
- `claude-voice plugins list` checks a plugin the way a restart does, so it no longer
  says a plugin is approved while the bridge refuses its changed tools.
