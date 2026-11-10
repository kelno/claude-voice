# ADR-CV-104: The side session works on past the caller's time budget

## Status

Accepted by the owner of the kelno/claude-voice fork, 2026-10-29. Amends ADR-CV-103. Rebuilt on upstream's per-client time budgets at the resync of 2026-11-06.

## Context

- The Claude app drops a connector's tool call that runs for about 30 seconds, and the
  assistant gets only "MCP tool call failed". Measured on 2026-10-29: calls of up to 28.6
  seconds came back; an `ask_active_session` held open about 76 seconds, and a side-session
  call of about 30 to 40 seconds, were dropped. The bridge, still working, never noticed.
- The fork first cut every waiting call at 20 seconds. Upstream later gave each client a
  time budget of its own (20 seconds for the Claude app, two minutes for its voice app, a
  minute otherwise), and the fork takes those budgets instead of its own cap.
- `ask_forked_session` held the call open until the fork answered, whatever the budget.
- The bridge logged a finished call, never one the client abandoned.

## Options considered

- **Keep long waits and hope the client waits.** It doesn't.
- **Hold the side session to the budget, and drop its answer when time runs out.** A fork
  often needs a minute or more to read the code; its answer would be lost.
- **Every waiting call stays within the caller's budget, saying past it that the work is
  still going and how to read on; the side session works in the background.** Chosen.

## Decision

- Every waiting call stops at the caller's time budget, whatever it asks for: past it,
  `still_working` and a way to read on.
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

`src/claude_voice/server.py`, `src/claude_voice/fork.py`, `src/claude_voice/config.py` (the budgets), ADR-CV-103.
