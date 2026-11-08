# ADR-CV-014: The owner confirms with a passkey on the bridge's own page

## Status

Accepted by the owner of the kelno fork, 2026-11-07, after a live test with the owner's passkey passed; with the plan for the page: the page shows the
request behind its long random link, and one passkey prompt confirms it. Amends ADR-CV-013, whose card
can't carry the proof. The page's path is open to the owner's VPN only.

## Context

ADR-CV-013 rested the owner's confirmation on the Claude app's approval card. Tests on 2026-11-06 showed
the card can be skipped without the bridge seeing it: in the desktop app's voice chat, a tool set to
"always allow" ran with no card, and the phone's card on "always allow" comes from a reported app bug
(anthropics/claude-ai-mcp#1087, #1223) that a fix could remove. A guarantee that a settings mistake or an
app update silently removes isn't one agents or the owner can trust. The owner's answer has to reach the
bridge by a path no model can produce.

## Options considered

- **The app's approval card** (ADR-CV-013). Skipped silently by "always allow" in the desktop app; on the
  phone it holds only through a bug. Dropped.
- **Elicitation: the bridge asks the owner during the call.** The owner's preferred way, but the Claude
  app's voice chat declares no support for it (anthropics/claude-ai-mcp#153, open). Not available today.
- **A panel in the chat (an MCP App) whose tap carries a secret no model sees.** Also preferred, and it
  works in the desktop app: the relay never received the secret, and the tap reached the bridge with it.
  But the phone app refuses to show the panel, and the owner mostly confirms from the phone. Not
  available today.
- **A bookmark to a page listing pending requests.** Dropped for a link in the chat, which is simpler to
  use.
- **A link in the chat, with a sign-in remembered on the phone, relying on the owner to check the domain
  (and that the real page never asks for the secret).** Puts the security on the owner's vigilance, like
  "check your links" elsewhere on the internet: fine for personal use, not clean for upstream. Dropped.
- **An Android notification with Accept and Refuse buttons (for example through ntfy) as the
  confirmation itself.** Weaker by design: anyone who can read the notification can act on it (the
  notification server, apps with notification access, the lock screen), and a tap carries no proof of
  who made it. It also needs extra setup and an extra service for every user, a real cost in the owner's
  model, not a side note. Dropped.
- **A link in the chat to a page on the bridge's own domain, confirmed with a passkey.** Chosen, as a
  stopgap until elicitation or the panel works on the phone.

## Decision

- The relay gives the owner a link, in the chat, to the bridge's own page for the request.
- The page shows the session's request as the bridge stored it, and the owner confirms it with a passkey
  (WebAuthn, with user verification). The passkey is bound to the bridge's domain, so a look-alike page
  gets nothing usable, and nobody has to check the link's domain.
- The bridge marks the request confirmed only on a passkey assertion it verified itself, over a
  challenge it made for that request.
- The page is plain and minimal: only its security properties matter.

## Consequences

- The proof no longer rests on the app's settings or on what it chooses to show.
- The owner registers a passkey once, at the PC.
- The server proxy opens the page's path to the owner's phone; the owner deploys that change.
- When elicitation or the panel works on the phone, this page can be replaced by it.

## Related

`docs/owner-confirmation.md`, ADR-CV-013.
