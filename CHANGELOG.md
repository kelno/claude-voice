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
  sessions the owner can also open on the phone and at the PC.
- A background session waiting on a permission prompt shows what it asks.
- `start_active_session` can choose the model and effort of the session it
  starts; a model Claude Code doesn't know is reported in the first reply.
- `whats_new`, a cheap delta of what happened since the last poll.
- `claude-voice check` and `claude-voice serve`, and a user service installer.
