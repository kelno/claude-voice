# Changelog

Every release, what changed in it, and why. Dates are the tag's date. The
format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
versions follow [semantic versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added

- An MCP server that starts, follows, resumes and stops Claude Code sessions
  through the Claude Agent SDK, on the owner's subscription.
- Sign-in with OAuth for phone apps, and the login secret as a bearer token for
  local tools.
- Risky actions wait for a spoken approval and are refused after ten minutes.
- Sessions open elsewhere can be listed, messaged, read and searched.
- `create_session` is gone: new work starts with `start_active_session`, whose
  sessions the owner can also open on the phone and at the PC. They start in the
  background on Remote Control unless one start asks for a terminal window.
- A background session waiting on a permission prompt shows what it asks.
- `attach_conversation` resumes an earlier conversation as a background session,
  on Remote Control and in its own permission mode and folders.
- `whats_new`, a cheap delta of what happened since the last poll.
- `CLAUDE_VOICE_ACCESS_TOKEN_SECONDS` sets how long an access token lasts, for a
  bridge on a computer that sleeps: the Claude app gives a connector up after about
  an hour of failed token refreshes.
- The log is kept in `bridge.log` next to the database as well as on the console,
  with the outcome of every token refresh, so a connector that dropped while
  nobody watched can be diagnosed afterwards.
- `claude-voice check` and `claude-voice serve`, and a user service installer.
