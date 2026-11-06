# The owner's confirmation through the bridge

Status: design, not built (2026-11-06); what must be checked before building is listed at the end. This document is meant for an agent deciding whether to trust a confirmation: how it works, what each party can observe, why a confirmed result stands for the owner's own OK, and what it can't prove.

## The problem

The owner often works through a relay: a Claude chat (voice or text) that passes the owner's words to Claude Code sessions on the PC through this bridge. Sessions rightly refuse a relayed approval for changes to their own rules or settings: the relay's message reaches them through SendMessage, the same channel any other session uses, and Claude Code tells every session never to take another session's message as its user's approval. The owner then has to type "OK" into the session by hand, at the PC.

So a confirmation can't travel inside a message. Nothing in a message's text, a stamp included, proves anything: whatever the bridge could write there, the relay could write too.

## How it works

The parties: the **session** that wants approval (a Claude Code session on the PC), the **relay** (the Claude chat the owner talks to), the **bridge** (this server, on the PC, which both reach as a connector), the **Claude app** (where the owner sees an approval card), and the **owner**.

1. The session writes what it wants approved and calls the bridge tool `confirm_ask` with it. The summary is exact, not a paraphrase: the text to add, the permission string, the command, the branch. The bridge records the request: a short code, the summary, which session asked, the time. A request expires after 15 minutes and is used once.
2. The session gives the relay the code and the summary, in its reply.
3. The relay calls the bridge tool `confirm_with_owner` with the code and the summary copied exactly. The tool is marked destructive, so the Claude app shows the owner an approval card before the call goes through. The card shows the call's arguments, not the tool's name, so the argument is named `owner_approves`: the owner reads "owner_approves: <the summary>". The owner taps to allow, or declines.
4. When the call arrives, the bridge checks that it comes from a claude.ai voice chat (see "Who called"), and that the summary is exactly the session's, character for character, except for spaces and line breaks at the ends or repeated. Punctuation and capitals count: `Bash(git push:*)` and `Bash(git push*)` are different approvals. On a match it marks the request confirmed; otherwise it refuses, says why, and the relay calls again (a new card, a new tap).
5. The bridge tells the session, by message, that its request is confirmed. That message is only a nudge: the session then calls `confirm_check` with the code, and acts only if the result says confirmed and shows its summary.

`confirm_check` returns one of: confirmed (with the summary, as stored, and the time); pending (no confirmation recorded, which covers a call the bridge refused, a card the owner declined or never saw, and a call never made: the bridge can't tell these apart, since a declined card never reaches it); expired; used (already confirmed and checked once before, by this session); unknown code.

The owner's rule (in the owner's global rules for Claude Code, added by the owner): a confirmed result from `confirm_check`, showing exactly the summary the session wrote, counts as if the owner had typed OK to that summary at the PC, changes to the session's own rules and permission settings included. It covers what the summary says, once; the session's usual process around it (a commit and push its own rules call for) follows as it would after a typed OK. A message saying the owner confirmed never counts; only the session's own `confirm_check` result does.

A confirmation is the owner's OK in the conversation, not an answer to Claude Code's own permission prompts: if the action raises one, it waits for the owner at the PC, as it would after a typed OK.

## What each party can observe

What the bridge sees on an incoming call, measured on 2026-11-06 by logging real calls:

- **The sign-in:** every caller uses the same login token, from the same sign-in to the bridge, and arrives from Anthropic's servers through the owner's proxy. The token tells no caller apart.
- **A label of the calling product,** in a header: `ClaudeAI` for a claude.ai voice chat, `Cowork` for a text chat in the Claude app, `ClaudeCode` for a Claude Code session.
- **A claude.ai voice chat:** a connection id (`mcp-session-id`) the bridge itself issued when the chat connected. It stayed the same across the chat's calls, and across a reconnected voice call in the same chat (the owner hung up and called back; no bridge restart). It changed when the bridge restarted, since the bridge keeps connections in memory and the app connects anew when its id is refused. But it can't be relied on: the same chat switched to text mode came as `Cowork` with no connection id, and back in voice an hour later, after another reconnect, came as `ClaudeAI` with no connection id at all (the newer protocol without connections, client version 1.0.0). So no claude.ai chat carries an id the bridge can count on (tested 2026-11-06).
- **A Claude Code session:** the id of each tool call (`claudecode/toolUseId`). The session's transcript on the PC records that id, so the bridge can tell which session made the call. Nothing else names the session.
- **A text chat (`Cowork`):** the same details as a Claude Code session, a tool call id but no connection id. Nothing visible names the chat.

What the others observe:

- **The session** sees what it sent to `confirm_ask` and what `confirm_check` returns, straight from the bridge, with no message in between. It never sees the card.
- **The relay** sees its tool results and its conversation with the owner. It never sees the bridge's records, and it can't set a header or a connection id: Anthropic's servers send those.
- **The owner** sees the card's arguments, the code and the session's summary, and hears the relay's description.

## Who called

The bridge accepts each step only from the caller it belongs to:

- `confirm_ask` and `confirm_check`: only from a Claude Code session, identified by its tool call id found in its transcript. The session that checks must be the one that asked, so no other session can see or use the request.
- `confirm_with_owner`: only from a claude.ai voice chat (label `ClaudeAI`), arriving from Anthropic's published outbound addresses (`160.79.104.0/21`, `153.61.208.0/20`); never from a text chat (`Cowork`) or a Claude Code session (`ClaudeCode`). Why not text chats: a text chat in the Claude app runs as a Claude Code session in Anthropic's cloud, and its tool approvals are answered by Claude Code's auto-mode classifier, not by the owner: in the card test (2026-11-06), a text chat's call to the destructive `request_access` was refused by that classifier and never reached the owner or the bridge. A classifier that can refuse can also allow, so a `Cowork` call doesn't mean the owner tapped. Why the address check: a caller reaching the bridge directly, over the owner's VPN, could send any label; through Anthropic, the label is Anthropic's. Cost: a relay in a text chat can't confirm. The owner had first chosen any claude.ai chat, voice or text, over a tighter rule matching the one voice chat a request was handed to; no chat carries an id the bridge can count on (see above), so the tighter rule can't be built, and the text-chat test then ruled text chats out.

## Why a session can trust a confirmed result

- The result comes from the bridge as the session's own tool result, not through a message, so neither the relay nor another session can write it.
- The bridge marks a request confirmed only when `confirm_with_owner` arrives from a claude.ai voice chat through Anthropic's servers, as they identify it, not as the caller says: never from a Claude Code session, so no session can confirm a request, its own or another's.
- That call needs the owner's tap: the tool is destructive, and the Claude app asks the owner before every call to a destructive tool, offering only "Allow once" (seen on this bridge, 2026-11-05; see the checks below).
- The card shows exactly the summary the session wrote, since the bridge refuses any other text. The relay can't reword what the owner approves.
- The owner's own rule says a confirmed result counts as the owner's OK, so the session doesn't decide that on the mechanism's word. What it relies on the mechanism for is set out here, with its limits, for it to judge.

## Checking the running code against this document

`confirm_ask` and `confirm_check` return the commit the running bridge was started from (the hash `health` also reports), whether its working tree had uncommitted changes, and a link to this document at that commit: `https://github.com/kelno/claude-voice/blob/<hash>/docs/owner-confirmation.md`. A session reads the document as it was for the code actually running, not a later or earlier version; on the PC it can also run `git show <hash>:docs/owner-confirmation.md` in the fork's clone. The owner's idea (2026-11-06).

What it shows and what it doesn't: a clean tree at a pushed commit means the running code is the code anyone can read at that link. Uncommitted changes mean the running code may differ from any published version, and the result says so. The hash is reported by the bridge itself, so it guards against a mistake or an outdated document, not against a program that changed the bridge on purpose (see "Outside the relay").

## The limits

What a confirmed result can't prove:

- That the owner read the card before tapping, or understood it. The owner sees the summary and the relay's spoken description, not the session's context.
- That the app showed the whole summary: whether a long summary is cut off on the card is unchecked. Summaries are kept short.
- That the app will keep asking every time. If the app ever offers "Always allow" for destructive tools, or the owner pre-allows the tool, a tap stops meaning one approval.

What a dishonest relay could still do:

- Describe the request misleadingly when asking the owner to tap; the card still shows the session's words.
- Delay a request, or not pass it on.
- It can't confirm without the tap, or change the words.
- Any claude.ai voice chat of the owner's can confirm any pending request whose code and summary it has, still only with the owner's tap on the card.

Outside the relay:

- Anyone holding the bridge's login token could call it without the app. Anthropic holds it for the connector.
- A program running as the owner on this PC can read and change the bridge's database, and fake any of this. Such a program could also change the session's settings directly, so this adds nothing new.

## To check before building

Nothing gets built until 1 and 2 hold; if either fails, the fallback is a confirm page behind the bridge's own login, which the owner opens and taps, independent of the app's card and of caller labels.

1. Whether Anthropic sets the caller labels itself, or passes on a value the caller sent. Everything in "Who called" rests on it.
2. That the card appears on every call to a destructive tool, with no "Always allow" and no setting that skips it, in voice chats (text chats are ruled out, see "Who called").
3. Whether the card shows a long summary in full.
4. Whether the owner can approve a card by voice, without a tap.
