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
  `session_recap`, `wait_for_session`, `cancel`, `close_session`,
  `list_sessions` and the approvals) are gone: new and resumed work runs as
  background sessions the owner can open on the phone and at the PC, and
  answers permission prompts there.
- A background session waiting on a permission prompt shows what it asks.
- `wait_for_session` waits, sending nothing, until a session's turn ends or it
  needs input, so a voice assistant can wait for a reply without asking again.
- `attach_conversation` resumes an earlier conversation as a background session,
  on Remote Control and in its own permission mode and folders.
- `start_active_session` can choose the model and effort of the session it
  starts; a model Claude Code doesn't know is reported in the first reply.
- `whats_new`, a cheap delta of what happened since the last poll.
- `claude-voice check` and `claude-voice serve`, and a user service installer.
