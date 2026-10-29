# ADR-CV-009: No tool call waits longer than twenty seconds

## Status

Accepted by the owner of the kelno/claude-voice fork, 2026-10-29. Amends ADR-CV-008.

## Context

- The Claude app drops a connector's tool call that runs for about 30 seconds, and the
  assistant gets only "MCP tool call failed". Measured on 2026-10-29: calls of up to 28.6
  seconds came back; an `ask_active_session` held open about 76 seconds, and a side-session
  call of about 30 to 40 seconds, were dropped. The bridge, still working, never noticed.
- `ask_active_session` and `wait_for_session` allowed waits of up to 300 seconds, and
  `ask_forked_session` held the call open until the fork answered.
- The bridge logged a finished call, never one the client abandoned.

## Options considered

- **Keep long waits and hope the client waits.** It doesn't.
- **Shorter defaults, longer waits still allowed.** The assistant asked for 120 and 240
  seconds tonight; a limit has to hold whatever is asked.
- **A hard cap, past which every waiting call says the work is still going and how to
  read on; the side session works in the background.** Chosen.

## Decision

- Every waiting call is cut at 20 seconds, whatever it asks for, leaving a margin under
  the client's cutoff: past it, `still_working` (or `still_running`) and a way to read on.
- Open sessions and the side session have the same three ways in: send and return
  (`message_active_session`, `message_forked_session`), read later (`read_session_output`
  with the `next_after` a send returns, `read_forked_session` with a `fork_id`), and ask and
  wait (`ask_active_session`, `ask_forked_session`).
- A side session works in the background for up to five minutes; its answer is kept for an
  hour, in memory only.
- A call the client abandons is logged, with how long it ran.

## Consequences

- No call silently dies; the assistant always gets an answer or a way to read on.
- Worse: a slow answer takes more calls, and a bridge restart forgets side sessions at work.
- Merging send into ask was considered and refused: asking sends nothing to a session that
  waits for its user, so the owner could not answer a session's question by voice.

## Related

`src/claude_voice/server.py`, `src/claude_voice/fork.py`, ADR-CV-008.
