# ADR-CV-013: The owner confirms on the app's card, in a voice chat

## Status

Accepted by the owner of the kelno fork, 2026-11-06.

## Context

Sessions refuse an approval the relay passes on, rightly: it reaches them as
another session's message, which Claude Code tells them never to take as their
user's approval. The owner then had to type OK at the PC.

## Options considered

- **A stamp in the bridge's message.** Whatever the bridge writes in a message,
  the relay could write too. Refused.
- **Any claude.ai chat confirms on the app's card.** A text chat in the Claude app
  runs as a cloud Claude Code session whose tool approvals are answered by a
  classifier, not by the owner. Refused for text chats.
- **The tighter rule: only the chat the request was handed to.** No chat carries
  an id the bridge can count on. Can't be built.
- **A confirm page behind the bridge's own login.** Independent of the app's card;
  more to build, and the owner must look at a screen. Kept as the fallback.
- **The session asks and checks through bridge tools; the owner taps the card of a
  destructive tool, called from a voice chat through Anthropic's servers.** Chosen.

## Decision

- `confirm_ask` records a session's exact summary; the session is the one whose
  transcript holds the call.
- `confirm_with_owner`, marked destructive, counts only from a call labelled
  `ClaudeAI` coming from Anthropic's published outbound addresses, with the
  summary matching exactly, spaces aside.
- `confirm_check` gives the result only to the session that asked, once.
- The owner's own global rules say a confirmed result counts as their OK.

## Consequences

- The owner confirms from the phone, and a session can check it for itself.
- A relay in a text chat can't confirm.
- It rests on Anthropic setting the caller label and the app asking before every
  call to a destructive tool, both tested on 2026-11-06; if either changes, the
  confirm page replaces the card.

## Related

`docs/owner-confirmation.md`, `src/claude_voice/confirm.py`.
