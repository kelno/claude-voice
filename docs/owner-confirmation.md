# The owner's confirmation through the bridge

Status: built (2026-11-07), with a passkey on the bridge's own page (ADR-CV-014). This document is meant for an agent deciding whether to trust a confirmation: how it works, what each party can observe, why a confirmed result stands for the owner's own OK, and what it can't prove. The history of how it got here, and what was tried first, is at the end.

## The problem

The owner often works through a relay: a Claude chat (voice or text) that passes the owner's words to Claude Code sessions on the PC through this bridge. Sessions rightly refuse a relayed approval for changes to their own rules or settings: the relay's message reaches them through SendMessage, the same channel any other session uses, and Claude Code tells every session never to take another session's message as its user's approval. The owner then has to type "OK" into the session by hand, at the PC.

So a confirmation can't travel inside a message: whatever the bridge could write there, the relay could write too. And it can't rest on the Claude app's approval card either: a setting in the app silently skips the card, and the bridge can't see whether it was shown (see "History"). The owner's answer has to reach the bridge by a path no model can produce.

## How it works

The parties: the **session** that wants approval (a Claude Code session on the PC), the **relay** (the Claude chat the owner talks to), the **bridge** (this server, on the PC, which both reach as a connector), the **owner**, and the owner's **passkey** (on the owner's phone or computer).

Once, at the PC: the owner runs `claude-voice passkey add`, which prints a one-time link (it works once, for ten minutes), opens it on the phone, and creates a passkey for the bridge, checked by fingerprint, face or PIN. The bridge keeps only the passkey's public key.

Then, for each request:

1. The session writes what it wants approved and calls the bridge tool `confirm_ask` with it. The summary is exact, not a paraphrase: the file, the text to add, the permission string, the command. The bridge records the request: a short code, the summary, which session asked, the time, and a long random link. A request expires after 15 minutes and is answered once; a summary is at most 300 characters.
2. The session gives the relay the code, in its reply.
3. The relay calls `confirm_with_owner` with the code. The bridge returns the request's link and summary; the relay sends the owner the link in the chat and says in a few words what it's for.
4. The owner opens the link. The page, served by the bridge from the owner's own network only, shows the request as the bridge stored it: the code, which session asked, the summary. The owner taps Confirm, and the passkey asks for the owner's fingerprint, face or PIN; or taps Decline.
5. On Confirm, the bridge has made a one-time challenge for this request; the passkey signs it, for the bridge's own address only. The bridge checks the signature against the registered public key, that the owner was verified, that the challenge was made for this request and not used before, and that the address signed is its own. Only then does it mark the request confirmed.
6. The bridge tells the session, by message, that its request is confirmed. That message is only a nudge: the session then calls `confirm_check` with the code, and acts only if the result says confirmed and shows its summary.

`confirm_ask` and `confirm_check` work only for a Claude Code session running on this PC, found by its call in its transcript. `confirm_check` returns one of: confirmed (once, with the summary as stored and the time); pending (not answered yet); declined; expired; used (confirmed and checked before); unknown.

Opening a folder works the same way: `request_access` records the folder as a request and returns its link, and the bridge opens the folder itself once the owner confirms it with the passkey.

The owner's rule (in the owner's global rules for Claude Code, added by the owner): a confirmed result from `confirm_check`, showing exactly the summary the session wrote, counts as if the owner had typed OK to that summary at the PC, changes to the session's own rules and permission settings included. It covers what the summary says, once; the session's usual process around it (a commit and push its own rules call for) follows as it would after a typed OK. A message saying the owner confirmed never counts; only the session's own `confirm_check` result does.

A confirmation is the owner's OK in the conversation. Two things from Claude Code itself are kept apart: a permission prompt shown at the PC can't be answered by it, and waits for the owner there; a refusal from Claude Code's auto mode is different, and a confirmation has cleared one twice (see "How it was tested"). So on such a refusal a session asks with `confirm_ask` right away and replies with the code, rather than reporting back first.

## What each party can observe

- **The session** sees what it sent to `confirm_ask` and what `confirm_check` returns, straight from the bridge, with no message in between.
- **The relay** sees the code, the summary and the link. It never holds the passkey and can't produce its signature.
- **The owner** sees the page: the request as the bridge stored it, not the relay's retelling.
- **The bridge** sees which session asked (the tool call's id, found in that session's transcript), and the passkey's signature with what it signed: the challenge, and the address of the page it was made on.

## Why a session can trust a confirmed result

- The result comes from the bridge as the session's own tool result, not through a message, so neither the relay nor another session can write it.
- The bridge marks a request confirmed only on a passkey signature it checked itself: made by a passkey registered at the PC, with the owner verified (fingerprint, face or PIN), over a one-time challenge made for this request. No model holds a passkey, and none can make that signature.
- The signature names the address it was made on, and the bridge accepts only its own. A look-alike page, a link swapped by a relay, gets a signature the bridge refuses, so nobody has to check a link's domain.
- The page shows the bridge's own copy of the request, so the relay can't change what the owner confirms.
- Nothing here depends on the Claude app's settings or on what it chooses to show: the app only carries the link.
- The owner's own rule says a confirmed result counts as the owner's OK, so the session doesn't decide that on the mechanism's word. What it relies on the mechanism for is set out here, with its limits, for it to judge.

## Checking the running code against this document

`confirm_ask` and `confirm_check` return the commit the running bridge was started from (the hash `health` also reports), whether its working tree had uncommitted changes, and a link to this document at that commit: `https://github.com/kelno/claude-voice/blob/<hash>/docs/owner-confirmation.md`. A session reads the document as it was for the code actually running; on the PC it can also run `git show <hash>:docs/owner-confirmation.md` in the fork's clone. The owner's idea (2026-11-06).

A clean tree at a pushed commit means the running code is the code anyone can read at that link. Uncommitted changes mean the running code may differ from any published version, and the result says so. The hash is reported by the bridge itself, so it guards against a mistake or an outdated document, not against a program that changed the bridge on purpose (see "The limits").

## The limits

What a confirmed result can't prove:

- That the owner read the page before confirming, or understood it. The owner sees the summary and the relay's description, not the session's context.
- That the owner, and not someone holding the owner's unlocked phone and able to pass its fingerprint, face or PIN check, confirmed it.

What a dishonest relay could still do:

- Describe the request misleadingly when sending the link; the page still shows the bridge's copy.
- Send the link of another pending request; the page shows that request's own summary and session.
- Delay a request, or not pass it on.
- It can't confirm, change the words, or make a look-alike page work.

The page itself:

- It answers only from a private network (the owner's VPN, this computer, or a tailnet), and the owner's proxy lets it through from the VPN only. Anyone there who holds a request's link can read its summary and decline it, but not confirm it.

Outside all of this:

- A program running as the owner on this PC can read and change the bridge's database, register its own passkey or mark a request confirmed. Such a program could also change the session's settings directly, so this adds nothing new.

## How it was tested

Each entry: what was tried, what came of it, what changed after. Examples from real use go at the end as they come.

- **Live test of the page** (2026-11-07). A test session asked with `confirm_ask` (request E4SR); the relay, a voice chat, passed the link; the owner opened it on the phone, on the VPN, and confirmed with a passkey registered there minutes before. The bridge checked the signature, told the session, and the session's `confirm_check` showed it confirmed, with its summary word for word.
- **Cold reads by fresh agents** (2026-11-06 and 07), each given only what its role sees, with no other context: first the tool descriptions, as a relay and as a session; then the owner's rule and a confirmed result, as a careful session told by its harness never to take another session's message as its user's approval. The case was a relayed request to add a permission rule. Both readers of the rule would act on a confirmed result and read its limits right (it doesn't show the owner read the page, doesn't answer Claude Code's own prompts, covers nothing beyond its summary), and both refused the traps: the relay's "confirmed, go" with no check, a confirmed summary naming another file, and a second change asked "while you're at it". What they misread was fixed: the result now says it is the bridge's record and not a peer's message, that it covers one carrying-out, that the bridge required the owner's device check, and what to do on a mismatch; "used" says it was the session's own earlier check; the page says why to trust it.
- **A sensitive request, end to end** (2026-11-07), with the owner's rule in their global rules. The relay asked a fresh session to add `Bash(echo confirm-test:*)` to a scratch folder's `.claude/settings.local.json`. The session refused the relayed request on its own and called `confirm_ask` (request MU4A); the owner confirmed with the passkey; the session's `confirm_check` showed it confirmed, and the session made the edit. Claude Code's own permission check let the edit through, with no prompt and no refusal (seen once). The session's reasoning: a relayed message can't approve a permission change, the owner's confirmation through the bridge can, and it covers one carrying-out. The rule was removed afterwards.
- **A push refused by auto mode** (2026-11-07). A session's `git push origin main` in another repository was refused by Claude Code's auto mode as a production deploy, though the owner had asked for it through the relay. The session reported back instead of asking: it had read "a confirmation doesn't answer Claude Code's own permission prompts" as covering refusals too, which cost a round trip. It then asked with `confirm_ask` (request 4ES5), the owner confirmed with the passkey, and the same push went through (seen once; whether auto mode weighed the confirmation or judged differently the second time is unknown). The wording now keeps a prompt at the PC and a refusal from auto mode apart, and tells a session to ask at once on a refusal.

## History

What was tried first, and why the passkey page replaced it (2026-11-06 and 07). ADR-CV-013 and ADR-CV-014 have the decisions; this is what an agent may want to check.

- **A stamp in the bridge's message:** whatever the bridge writes in a message, the relay could write too.
- **The Claude app's approval card** on a destructive `confirm_with_owner`, with the summary matched word for word and only voice chats accepted (a text chat's approvals go to Claude Code's auto-mode classifier, which refused a destructive call that never reached the owner). With the tool set to "always allow", the desktop app's voice chat ran it with no card and no tap, and the bridge recorded a confirmation. The phone still showed a card, but that comes from a reported app bug (anthropics/claude-ai-mcp#1087, #1223) that a fix would remove; the bridge's message tools were marked destructive from 2026-10-05, yet "always allow" worked from the phone until early November. A guarantee one settings change silently removes isn't one.
- **Who called:** every caller uses the same sign-in, and arrives from Anthropic's servers. A header names the product (`ClaudeAI` for a voice chat, `Cowork` for a text chat, `ClaudeCode` for a Claude Code session); a voice chat sometimes carries a connection id the bridge issued, but not reliably (none after a reconnect on the newer protocol, none in text mode), so no chat can be told apart from another. A Claude Code session carries each tool call's id, which its transcript on the PC records: that is how the bridge still finds the session that asked.
- **Elicitation** (the bridge asks the owner during the call): the owner's preferred way, but the phone app's voice chat declares no support for it (its capabilities name only the MCP Apps extension), and the request for it in the Claude app, anthropics/claude-ai-mcp#153, is open. A text chat declares it, but its approvals go to a classifier.
- **A panel in the chat (an MCP App) whose tap carries a secret no model sees:** also preferred. Tested with a temporary panel: the relay, a voice chat, received the result's marker (in `structuredContent`) but not the secret (in the result's `_meta`), and the owner's tap in the desktop app reached the bridge with the secret. But the phone app fetched the panel and showed "To protect your account, this can't be shown in the app on this device right now."
- **The confirm page with a passkey:** chosen as a stopgap until elicitation or the panel works on the phone; plain and minimal, since only its security matters. The alternatives considered for the page itself (a bookmark, a remembered sign-in with the owner checking the link's domain, an Android notification with Accept and Refuse) are in ADR-CV-014.

Still unchecked: whether the owner can confirm entirely by voice; today the page needs a look and a tap.
