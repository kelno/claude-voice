# ADR-CV-107: An idle session is parked rather than stopped

## Status

Accepted as a trial by the owner of the kelno/claude-voice fork, 2026-11-17: parking stays while it proves itself in use, and sessions waiting on a permission prompt or a question are stopped at once, as built. Checked live on 2026-11-17 and 18 ("The live check", below): a parked session stays listed once Claude Code closes it, but a message from the app doesn't wake it. Proposed 2026-11-14 by an agent working for the owner, and reviewed the same day by a second agent.

## Context

- `stop_active_session` stops a background session with `claude stop <job>`. Claude Code's background service then ends the session's process (SIGTERM, its `kill` operation), and the session's normal exit closes its Remote Control link and archives its claude.ai session. The session disappears from the owner's session list in the Claude app, on the desktop and the phone, so it can no longer be seen there.
- Claude Code also closes background sessions itself, once they have been quiet long enough: an hour after a session last worked, 8 hours when it has Remote Control, a minute when memory runs low. The time counts from the last update of the session's job record, not from any stop. It closes a session at rest, or one whose job record says it waits on its user; never one at work, or one with a task it started still running. That close sends the session a "shutdown" message instead; the session closes its Remote Control link without archiving it, records where its stream stopped, and exits. The session stays in the app's list, shown offline; a message the owner types to it there waits unseen until something resumes it (checked on a parked session, "The live check" below).
- How this was found, in Claude Code 2.1.325: its bundled code (`retireIfSettled` and `shutdownWorker` in the background service, and the session's `shutdown` handler, which tears its Remote Control link down with `skipArchive: true`; the stop command and the service's `kill` operation, which don't), and the service's own log, `~/.claude/daemon.log`, where a low-memory close reads "bg retire …: settled, idle 4h [low memory]" then "bg settled … (done)", and a bridge stop reads "bg settled … (killed)". The job records in `~/.claude/jobs/<id>/state.json` look alike either way ("done", at rest).
- Claude Code offers no command for its own close. `claude stop` is the only stop; the service's control socket has `kill` and no retire. The "shutdown" message travels on a private socket between the service and the session, behind a key the session deletes once read, and the session admits one peer at a time.

## Options considered

- **Do nothing.** A stopped session stays out of the app's list.
- **Un-archive the claude.ai session after the stop, through claude.ai's API.** The bridge would need the owner's claude.ai sign-in, and un-archiving asks for elevated authentication from a trusted device, which Claude Code itself doesn't always get; the API is undocumented.
- **Send the session the "shutdown" message itself.** It needs the key the session deleted, and connecting would cut the background service's own link to the session; an internal protocol any release may change.
- **Ask Anthropic for a stop that keeps the session listed** (a `claude stop` option doing what the low-memory close does). The right fix, and worth asking for, but not ours to make; nothing changes meanwhile.
- **Park a session waiting on a permission prompt or a question too.** Not taken for now. Claude Code closes a session whose job record says "blocked" with tempo "blocked", and treats one with tempo "blocked" but another state as not settled, so never closes it; the bridge sees only the live status "waiting", which doesn't tell the two apart, so a parked one might never be closed. And the news line on every result names each session waiting on a permission prompt, so a parked one would be named for hours. A session that ended its turn and only declared it waits for the owner (as Claude Code's log showed closed on 2026-11-14, "idle-prompt") reads as idle to the bridge and is parked.
- **Park an idle session: leave it for Claude Code's own close.** Chosen.

## Decision

`stop_active_session` parks an idle background session that has Remote Control: it answers status `parked`, with a `say` for the owner, leaves the session running at rest, and Claude Code closes it itself, keeping it in the app's list. With `now=true`, or for a session not at rest (at work, waiting on a permission prompt or a question, or with a background task still running), it stops the session at once, as before; so it does for a session without Remote Control or in a window, which has no place in the app's list to keep. `list_active_sessions` and `fleet_recap` mark a parked session `parked: true` until it works again; the mark is dropped once the session is no longer running.

## Consequences

- Worse: a parked session keeps its memory until Claude Code closes it, up to 8 hours after it last worked on a machine with memory to spare; under memory pressure Claude Code closes it within minutes. A session that has been quiet for hours is closed soon after it is parked.
- Worse: "stop" no longer means the process has ended. The app shows a parked session as running until Claude Code closes it; the result's `say` gives the relay the words for the owner.
- When Claude Code closes a session is its own business: the thresholds above come from its current code, not a documented contract, and a release may change them.
- After a PC restart, the bridge's wake-up of interrupted sessions leaves a parked session down, unless it worked after being parked or is named in `wake_at_start`. It stays listed in the app, shown offline, as Claude Code's own close would have left it. Parking only keeps a session in sight in the app; the owner had asked for it to stop, so it isn't brought back (owner, 2026-11-21, after a parked session came back at a restart). Until then the wake-up brought a parked session back like any session that was running. Left down, it counts as closed until it runs again: `list_active_sessions` names it under `offline_in_app`, and a message through the bridge resumes it first, as for a session Claude Code closed (owner, 2026-11-21: left down at first, it was in no list the relay reads, and a message to it failed).
- The `parked` label is lost, though the session is still parked, whenever Claude Code rewrites the session's registry entry with a status, even an unchanged one (its `statusUpdatedAt` is stamped on every such write), as at a respawn after a wake. Idle entries aren't otherwise rewritten while they rest.
- Once Claude Code has closed a parked session, `list_active_sessions` names it under `offline_in_app` ("List the sessions the Claude app shows offline"), like any session Claude Code closed: a message the owner types to it in the app waits unseen until it is resumed (seen in the live check), and the relay resumes it when the owner says they wrote to it. So parking keeps a session in sight in the app, not answerable there on its own.
- A parked session the owner talks to through the bridge, before Claude Code closes it, simply carries on, and is no longer marked parked. One the owner types to in the app before that close is still running on Remote Control, so it should do the same, but that isn't checked.
- The bridge's own stops that start the session again at once (a restart in place, `compact_after_turn`) still use `claude stop`.

## The live check (2026-11-17 and 18)

On throwaway sessions, after the change landed and the bridge restarted (ledger Q125 holds the details):

- Parking, the mark, a message through the bridge dropping the mark, and `now=true` stopping at once and taking the session off the app's list all worked as described.
- Claude Code closed the parked "parking test A" for low memory, 96 minutes after it last worked, not at 8 hours. It stayed in the app's list, shown offline, and `list_active_sessions` named it under `offline_in_app`.
- The owner then typed to it in the app: it didn't wake. A minute later its transcript was unchanged and `daemon.log` had no spawn. Only once it was resumed with `attach_conversation` did the owner's message arrive, and it answered.
- Not checked: a message from the app to a parked session before Claude Code closes it.

The owner's go for the rewording that followed (the parked result's `say`, `stop_active_session`'s description, this record): "we'll say go, and it doesn't need a test like that".

## The owner's answers (2026-11-17)

1. Park, as a trial: "let's try to park them then and let the app handle it, as a trial". The owner's worry was memory: Claude Code closes parked sessions within a minute or so once free memory drops under 1 GB (its own threshold, set remotely by Anthropic; ledger Q125), and otherwise about 8 hours after they last worked; neither time can be set.
2. Sessions waiting on a permission prompt or a question are not parked: "Fine as it is now".

## Related

`src/claude_voice/parking.py`, `src/claude_voice/server.py` (`stop_active_session`, `running`, `fleet_recap`); ADR-CV-108, under which any background session is parked alike, the owner's own too, `src/claude_voice/offline.py` (`offline_in_app`), `UPSTREAM.md`, `UPSTREAM-CANDIDATES.md`.
