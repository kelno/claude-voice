# ADR-CV-007: Desktop scratch folders can be opened, and access is named for what is missing

## Status

Accepted by the owner of the kelno/claude-voice fork, 2026-10-29. Amends ADR-CV-006.

## Context

- ADR-CV-006 keeps `AppData` in the home folder closed. On Windows the Claude desktop
  app makes a scratch folder for a quick session under
  `AppData\Local\Claude-Data\<package>\scratch-workspaces\<account>\<org>\scratch-<date>-<suffix>`
  (older ones under `AppData\Roaming\Claude\scratch-workspaces\…`). Such a session could
  never be reached by voice.
- The same app folder holds the app's own storage (`Local Storage`, `Network`,
  `IndexedDB` and more), which may hold credentials.
- In a test by voice, the assistant read `access: "needs_approval"` as the bridge's
  other kind of approval, a tool call waiting for a yes, and could only guess that it
  meant a folder not yet opened. A refusal said a folder "is, or holds, a folder kept
  closed" without naming it, so the owner could not tell why.

## Options considered

- **Open all of `Claude-Data`.** Opens the app's storage too.
- **Remove `AppData` from the closed folders.** Opens every program's data.
- **An exception for the scratch folders only, as a pattern of exact depth.** Chosen.
- **Keep the names, document them.** The clash would remain for every reader.

## Decision

- A path inside a closed folder is still opened if it is a desktop scratch folder, or
  inside one: a folder named `scratch-*` exactly three levels under a `scratch-workspaces`
  folder of the app's data, in either location. `scratch-workspaces` and every folder
  above it stay closed, and a grant opens one scratch folder at a time.
- The listed values are `access: "folder_not_open"` (open it with `request_access`) and
  `access: "folder_closed"` (never opened). The field is absent when a session is open.
- A refusal names the closed folder and the relation: the session's folder is inside it,
  or holds it.

## Consequences

- Quick desktop sessions can be reached once the owner opens their scratch folder.
- The pattern follows the app's current layout; if the app moves its scratch folders,
  the exception stops matching and they are closed again, which fails safe.
- Worse: each scratch folder has to be opened on its own, and they are made often.

## Related

`src/claude_voice/access.py`, ADR-CV-006, README "How it behaves".
