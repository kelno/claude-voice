# ADR-CV-108: The bridge handles every session, not only those it started

## Status

Accepted by the owner of the kelno/claude-voice fork, 2026-11-17. A standing rule for the fork: it also covers whatever upstream limits to the sessions the bridge started in the future.

## Context

- Upstream stops only a session the bridge started itself: "A session opened in a terminal, or started any other way, is refused even when named exactly" (upstream's "Stop a background session the bridge started, and no other", 2026-10-09). The fork's own features followed the same line: waking the sessions a shutdown interrupted, resuming a session Claude Code closed when a message comes for it, renaming a session in Claude Code itself (at once, or once a busy one is idle), restarting a session that came without its claude.ai connectors, and `compact_after_turn`.
- The owner has one Claude Code session, the Coordinator, that coordinates all their others, through the bridge. Each of those limits left it unable to handle a session the owner had started themselves. In the owner's words: "the bridge should be allowed to close session I opened now", "I want the coordinator to be able to handle all sessions", and, for the other limits, "Yes, bridge should be allowed to change all".
- Upstream may add more such limits; the owner asked that the fork's difference be a standing rule, so a resync catches them.

## Decision

1. The bridge does for any session what it does for one it started. Whether the bridge started a session no longer decides what it may do to it; the bridge's record of the sessions it started stays, for what it is still used for (finding a session's name and folder once it is no longer running).
2. What still limits it is what a session is, not who started it:
   - Only a session in a folder open to the bridge is reached at all, as before (ADR-CV-101).
   - Claude Code's background service renames, restarts, compacts and wakes only its background sessions; a session in a window can be stopped, by ending its Claude Code, and renamed in the bridge's lists only.
   - A session of the Claude desktop app is not stopped. Nothing outside the app closes one cleanly: the app starts it again quietly, or shows an error card, when its process is ended from outside, and offers no outside command to close it (desktop app 5.11492, read 2026-11-17).
   - A session a program runs through Claude Code's SDK is not stopped: it ends with that program.
3. A closed background session the bridge didn't start is found by Claude Code's job record for it (`~/.claude/jobs/<id>/state.json`), so a message to it resumes it like one the bridge started.
4. Standing rule: at each resync, any new upstream limit to "a session the bridge started" is widened the same way, and recorded in `UPSTREAM.md`. The fork's `CLAUDE.md` says so.

## Consequences

- Better: the Coordinator can stop, wake, resume, rename and compact any of the owner's background sessions, and stop one in a terminal.
- Worse: a session the owner is typing in, in a terminal, can be stopped from the bridge, and what they hadn't sent is lost. The check that a relay has read a session's reply before it is stopped still applies.
- After a PC restart, the owner's own background sessions that were running are woken too, not only the bridge's.
- A stop of a terminal session ends its Claude Code without its own exit steps (Windows can't ask a process to end gracefully from outside), so a Remote Control link it had may linger in claude.ai until claude.ai drops it.
- Each upstream resync has one more thing to check.

## Related

`src/claude_voice/every_session.py`, hooked into `src/claude_voice/server.py` (`stop_active_session`, `wake_interrupted`, `find_live_or_wake`, `not_running`, `rename_in_claude`, `rename_pending`, `compact_after_turn`) and `src/claude_voice/connectors.py`; `UPSTREAM.md`, "Same tools, different behaviour".
