# ADR-CV-009: Folders outside the root are opened one at a time, by the owner

## Status

Accepted by the owner of the kelno/claude-voice fork, 2026-10-29.

## Context

- The bridge only reaches folders under `CLAUDE_VOICE_ROOT`: it starts sessions
  there, and it lists, reads and messages only the running sessions there.
- An owner's sessions are not all under one folder. In a test by voice, a session
  open in the owner's notes folder was invisible, and the assistant had no way to
  know it existed.
- Inside its folder, a session reads files without asking. A session started in
  the owner's home folder could read SSH keys, browser data, or the bridge's own
  login secret without a single approval.
- The assistant calls `approve` itself, so the bridge's own approvals rest on the
  assistant having heard a clear yes. The Claude app, though, can ask the owner
  before it calls a connector's tool ("ask each time"), and only the owner can
  answer that prompt.
- Claude Code's registry of running sessions records one working folder per
  session. Folders added to a session later are not in it.

## Options considered

- **Widen the root**, for example to a whole drive. One setting, but every
  folder under it is open at once, the owner's synced folders included.
- **Several roots in the configuration.** Safe, but the owner has to know in
  advance and edit a file; nothing helps the assistant find a session elsewhere.
- **A tool that opens any folder by path.** The assistant would have to guess
  paths, and the owner would approve a path without seeing what lives there.
- **List every running session, and let the assistant ask for one.** Sessions
  outside the open folders are listed with only their name, folder and status,
  marked as needing approval. A tool asks to open that session's folder, and the
  owner sets that tool to "ask each time" in the Claude app. Chosen.

## Decision

- Every running Claude Code session is listed. One outside the open folders
  carries `access: "needs_approval"` and nothing beyond its name, folder, status
  and kind; it cannot be read, messaged or asked anything.
- `request_access(session)` opens that session's working folder and everything
  under it, for good: it is stored, and survives restarts. The same then holds as
  for the root: listed in full, readable, messageable, new sessions may start there.
- The grant is for the folder, not the session, because a session continued
  later gets a new id, and because a session's reach is decided by its folder.
- A built-in list of folders is never opened: under the home folder `.ssh`,
  `.gnupg`, `.claude`, `.config/claude-voice` and `AppData`, and the system's own
  folders. A session inside one, or in a folder that holds one, is listed as
  `access: "blocked"`, and no grant opens it. This holds inside the root too.
- `revoke_access(folder)` closes a granted folder again; `health` lists the grants.
- Later (2026-11-05): `request_access(folder=<full path>)` opens a folder no
  session runs in yet, so work can start remotely in a folder never used from
  the bridge. Same grant, same deny list, same approval in the app.
  The same approval also marks the folder trusted in Claude Code
  (`projects["<path>"].hasTrustDialogAccepted` in its config file, at the git
  repository's root when inside one, as its docs describe), since Claude Code
  refuses a background session in a folder it never trusted and asks only in a
  terminal. A closed repository root is never trusted.

## Consequences

- The assistant can find every session, and the owner decides, once per folder,
  what it may reach.
- The guard is only as strong as the app setting. If `request_access` is left on
  "always allow", the assistant opens folders on its own and the bridge cannot
  tell. The deny list is what remains then.
- Worse: names of sessions outside the open folders now reach the assistant. A
  side chat's name is the start of a question the owner typed.
- Folders a session was given later are not seen: approving a session shows its
  working folder only, while the session itself may reach more.

## Related

`src/claude_voice/access.py`, `src/claude_voice/sessions.py`, `src/claude_voice/server.py`, README "How it behaves".
