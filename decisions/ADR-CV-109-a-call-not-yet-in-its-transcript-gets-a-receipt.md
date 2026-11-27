# ADR-CV-109: A call not yet in its transcript gets a receipt

## Status

Accepted, 2026-11-28, by the owner of the kelno/claude-voice fork, after four reviews and with the risk "Consequences" names; proposed 2026-11-27 by the session fixing ledger F230. It changes how `confirm_ask`, `confirm_check` and `compact_after_turn` tell which session called them, which is the security check of the owner's confirmation (ADR-CV-106).

## Context

- The bridge tells which session made a call by finding the call's id (`claudecode/toolUseId`, which Claude Code sets, not the model) in the transcript of a Claude Code session running on this PC (`docs/owner-confirmation.md`, "Who called").
- Claude Code doesn't always write the call there before sending it. On 2026-11-27 one background session's calls reached its transcript 4.5 s late, then more than 6 s late, then, three times, only together with the call's result, over 21 s after it was sent, while the bridge looked for them for 20 s. A lone connector call in another session landed 0.3 s after it had returned, twice. When a shell command ran before the bridge got the call, the call was on disk in time; when Claude Code ran the same command only after the call returned, it wasn't. Why Claude Code holds the line back is not known (ledger F230, Q181).
- What does hold: the call's line reaches the transcript at the latest together with its result. So the bridge can always tell who made a call once it has answered it, but not always while it is answering.
- Claude Code has a `claudecode/sessionId` key for a call's `_meta`, but sends it only behind a feature flag that is off by default (read in Claude Code 2.1.332), so the bridge can't count on it.
- Claude Code passes a call an argument that its cached copy of the tool doesn't list (tested 2026-11-27), so a new optional argument reaches sessions that started before it existed.

## Options considered

- **Do nothing.** A session whose calls are held can't use the three tools at all; the owner types their OK at the PC instead. That's the failure this record is about.
- **Wait longer.** A call whose line lands only with its result is never found, however long the bridge waits; 20 s was tried and failed.
- **Ask the session to run a shell command with the call.** It helped only when Claude Code happened to run the command before sending the call, which the session can't control: tried, and refused again.
- **Trust `claudecode/sessionId`.** Not sent by default.
- **Create the request first and tie it to its session after answering**, with no retry. That works for `compact_after_turn`, but `confirm_check` has to give its verdict in its answer, and `confirm_ask` has to name the session in the line for the relay, so neither can wait until after.
- **Accept a retry because it repeats the same arguments** (the same code, the same summary). Others know a code: it travels through the relays, so a relay whose own call was held could read another session's verdict.
- **A receipt** (chosen).

## Decision

When a call carries a call id but no running session's transcript holds it after a short search (3 s), the bridge doesn't refuse it: it answers with status `not_seen_yet` and a receipt, a random string shown only in that answer, and does nothing else. The session calls the same tool again with the same arguments and `receipt` set to it. By then the first call is in the caller's transcript, written with its answer, so the bridge looks for the first call's id, and acts for the session whose transcript holds it, just as if that call had been found in time. A receipt works once, for the tool that gave it, with the same arguments, and for two minutes; a retry takes it at once, so two retries at the same time can't both use it. The retry also looks for its own call id, up to the same 3 s: found in another session's transcript than the first call's, once that one is found too, the retry is refused and the receipt stops working; found in the same, the retry is answered at once; not found, it is answered when the 3 s end. A receipt brought again after a retry took it gets an error saying another call used it, and a warning in the log. A transcript holds a call only where Claude Code itself recorded it, as the call in the model's turn or in its data-flow record: an id a tool's input or result only quotes doesn't count, since any session can write one into its own transcript, and a call two running transcripts record belongs to neither. This holds for every call the bridge looks up, not only a retry's. A receipt whose call is still in no transcript gives `not_seen_yet` again, with the same receipt, until it expires, and from the retry on its answer says that a subagent's calls are never found, so a subagent should stop and have its session make the call. A call with no call id at all is still refused at once.

## Consequences

- The evidence is the same as before, a running session's own transcript holding the call's id; only when it is looked for changes, and the receipt says which call to look for.
- A session whose calls are held makes each of these calls twice, a few seconds apart, and its retry waits the full 3 s when its own call is held too.
- A receipt is something a session can present by passing an argument, and it is in the session's transcript, which other Claude Code sessions on this PC can read. A call id, by contrast, Claude Code sets itself, so before this a session could act as another only by reaching the bridge some other way with a real call id: with the bridge's login secret, which sessions can read as well. What is left, for a caller that reads another session's transcript: within the two minutes and before the session it was given to retries, it can use the receipt once, but only to repeat that session's exact call, and only when its own call isn't seen in another running transcript, which gets it refused. That is by luck for a session whose own call is also held, but every time for a subagent, and any session can start one, so any caller that can read the session's transcript can use its receipt every time. It could read one `confirm_check` verdict meant for that session, make that session's exact `confirm_ask` request, whose code and link it then holds, or schedule that session's compaction with that session's own instructions; the session then gets the "another call used it" error and is told to tell the owner, and the log shows it. Before the stricter match above, a session could also write the first call's id into its own transcript through a tool's input and be taken for the caller; that is closed. Writing a line in Claude Code's own shape into a transcript file, as any session that can write files can, is not: the match checks a line's shape, not who wrote it. Such a session can move its own call's line into another session's transcript and be taken for it, with or without a receipt, or copy another session's call into its own so that call belongs to neither, which denies that session the three tools. The boundary that closes the rest is keeping sessions from reading and writing each other's transcripts, and from reading the bridge's login secret.
- Not done: checking, when the owner confirms on the page, that the retry's own call landed in the same transcript as the first, or watching for it after the answer and withdrawing the request otherwise. Either would close the `confirm_ask` case above, not the `confirm_check` one, whose verdict is already given; the first could block a real confirmation once the transcript has grown past what the bridge reads.
- A subagent's call no longer counts as its parent session's through an id that the parent's transcript only quotes; it still does where Claude Code lists the call in the parent's data-flow record.
- Worse: receipts live in the bridge's memory, so a restart between the two calls loses them, and the retry starts over with a new receipt.
- Three tools gain an optional argument and a status, which their descriptions name.

## Related

- `docs/owner-confirmation.md`, "How it works" and "Who called"; ADR-CV-106.
- Ledger F230 and Q181.
- Not yet measured: how soon after the first call's answer its line is on disk in the session that had held calls. Every test writes it before the retry; the "by its receipt" log line says how far into the retry's search it was found, for the first live test.
