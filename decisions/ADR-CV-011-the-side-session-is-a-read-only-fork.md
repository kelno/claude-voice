# ADR-CV-011: The side session is a read-only fork, made anew for each question

## Status

Accepted by the owner of the kelno/claude-voice fork, 2026-10-29.

## Context

- While a session works for a long time, a voice assistant that asks it something waits
  for the session's turn to end, and the voice conversation stalls.
- `peek_session` and `digest_session` answer from the conversation's words, read by a small
  model with no tools: they cannot look at the code, nor at tool results.
- The Agent SDK can resume a conversation as a fork (`fork_session`): a copy holding the
  whole conversation so far, which then goes its own way while the original is untouched.
- A session's permission callback is only asked about tools the owner's own rules don't
  already allow; a PreToolUse hook runs before every tool, whatever those rules say.
- A model's prompt is cached by its beginning: system prompt, tool list, conversation.
  Changing the permission mode or the tool list changes that beginning.

## Options considered

- **A subagent given a brief.** Starts without the conversation; its answers do not come
  from the same work.
- **A Claude desktop side chat.** A real fork, but the bridge cannot start desktop
  sessions, nor set their permissions.
- **A fork through the SDK, kept read-only by plan mode or a cut tool list.** Changes the
  prompt's beginning, so a second question cannot reuse the first one's cache.
- **A fork through the SDK, a new one per question, kept read-only by a hook and the
  permission callback, with the prompt saying so too.** Chosen.

## Decision

- `ask_forked_session(session, question)` forks the running session's conversation in its
  own folder, with the owner's own settings, sends the question with a note on who asks
  and who reads, and returns the fork's answer and the tools it used.
- Every tool outside a short list of reading tools (Read, Grep, Glob, LS, NotebookRead,
  ToolSearch) is refused by the hook, commands included, since they can write as well as
  read. The permission callback refuses the same, so nothing waits for an approval.
- Each question makes a new fork, so it starts from the latest state of the conversation.
- The fork is never shown in the desktop app; the owner accepted that as an exception to
  every relayed session being reachable, since it changes nothing.

## Consequences

- The assistant can ask about a busy session's work, code included, and get an answer
  in its own time while the session works on.
- Each question costs about a turn of a long conversation. The first fork of a desktop
  session likely misses the prompt cache, since the desktop app gives its sessions tools a
  fork does not have; later forks share their own beginning. To be measured.
- Worse: a fork cannot run a command even to read, such as `git log`; it has to read files.
- The forks' conversations stay in Claude Code's history, in the session's folder.

## Related

`src/claude_voice/fork.py`, `tests/test_fork.py`, ADR-CV-004.
