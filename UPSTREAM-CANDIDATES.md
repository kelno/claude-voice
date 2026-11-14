# What could go back upstream

Fork features that could go back to [claude-voice](https://github.com/roykollensvendsen/claude-voice) as pull requests, why upstream would want each, the pushback to expect, and how upstream's test-first rule applies. How the fork differs from upstream today, upstream's rules for a pull request and how one is checked here are in `UPSTREAM.md`. Fork commits are named by their subject, not their hash (find one with `git log --grep`). Upstream moves fast; check what it has taken before offering anything. Its author invited pull requests in [discussion #97](https://github.com/roykollensvendsen/claude-voice/discussions/97).

## Most promising (ideas)

Beyond the open pull request (candidate 9), the fork features most likely to help upstream, most promising first. Ideas only: nothing prepared. Each points to its candidate entry below.

0. The relay waits on a session and carries on by itself (owner: "a big improvement over upstream"). Upstream now has `wait_for_session` with `after`; the pieces still to offer: `message_active_session` returning `next_after`, so a send hands the wait its starting point (6d'; upstream's send returns none, so the relay has to read first); the long wait, whose result wakes the assistant once voice mode has moved it to the background (6l); the manual's "Waiting" rules, wait again on "still_working", in silence, answer the owner if they speak (6l, 6e); the `pending` note on a session already being waited on (6m). The move to the background itself is the Claude app's, not the bridge's. Guess: the news line on every result helps the relay notice replies but isn't what makes it wait. Offer `next_after` on a send first, then the long wait and the waiting rules.
1. The owner's confirmation with a passkey (10): a session asks the bridge for the owner's own OK to an exact summary, and the owner gives it on the bridge's own page with a passkey, from the phone. Any upstream user who works through a relay hits the same wall: Claude Code rightly refuses an approval that arrives as a message, so changes to a session's own rules or settings wait until the owner is back at the computer.
2. Messages to a session not marked destructive (6k): the Claude app asks for a tap before every message and offers only "Allow once", which every voice user of upstream hits on every message.
3. The manual handed to a conversation that never read it (6n, with the `guide` tool, 6e): the Claude app doesn't pass server instructions to the model, so upstream's instructions likely never reach its Claude-app users either.
4. The long wait (6l): voice mode moves a call past about 10 seconds to the background and its result wakes the assistant later, so one wait to the caller's limit carries the whole wait loop. Upstream's 60 s default under its 10 s budget for the Claude app already runs to that limit; the gain needs a larger budget for the app (the fork runs with 180 s), which is upstream's call.
5. Starting sessions in the background on Remote Control (6x): solves the problem upstream's new terminal window solves, with no desktop, terminal or tmux needed, on every platform.
6. Waking the sessions a shutdown interrupted (6s): upstream's bridge also runs on computers that sleep or restart, and today the interrupted sessions just stay stopped.
7. A read that starts the turn it has no room for (6i): upstream's `latest` field covers the far-behind case, but a long reply after a short turn can still come back as "nothing new".
8. The read-only side session (6d): upstream has only the quick look from what a session wrote; this one can read the code, at a cost upstream would want to weigh.
9. Folders opened by path, with Claude Code's trust (6o, 6o'): upstream now trusts folders it makes; opening an existing folder anywhere on the computer is the next step, though it writes Claude Code's config file, which upstream may resist.

## Candidates

In the order they'd best be offered. Each fork commit already follows upstream's rules (test first, gates green, message style), so each is a starting point, not a finished pull request.

### 5. Side chats marked — fork "Mark the desktop app's side chats in the session lists"

- What: running sessions whose name starts with "btw:" carry `side_chat: true` in `list_active_sessions` and `fleet_recap`; the instructions say not to send work there unless asked.
- Why: the Claude desktop app (macOS too, so not Windows-specific) runs a side chat as its own session, and the assistant can't tell it from a project session.
- Weakness: it relies on the app's naming, not a field (the session registry has none). Say so in the pull request; upstream may prefer to wait for a real field from Claude Code.
- Test: `test_a_desktop_side_chat_is_marked_as_one`.

### 6. Folders outside the root, opened by the owner — fork "Let the owner open folders outside the root, one at a time"

- Follow-up, fork "Name access states plainly and open desktop scratch folders" (ADR-CV-102): values renamed `folder_not_open` / `folder_closed` (the first collided with tool-call approvals), refusals name the closed folder and the relation (inside / holds), and an exact-depth exception opens the Claude desktop app's scratch folders under `AppData` (`# RULE` "only a Desktop scratch folder is opened inside a denied folder"). The exception is Windows-desktop specific; upstream may want it behind a setting.
- What: every running session is listed; one outside the root and the granted folders shows only name, folder, status, kind and `access` (`folder_not_open` or `folder_closed`), and can't be read or messaged. `request_access(session)` grants that session's working folder and everything under it, stored for good; `revoke_access(folder)` closes it; `health` lists grants. A built-in deny list (home `.ssh`, `.gnupg`, `.claude`, `.config/claude-voice`, `AppData`, and system folders) is never opened, nor any folder holding one, even inside the root.
- Why: an owner's sessions aren't all under one folder, and the assistant couldn't even tell a session existed. The approval comes from the owner's passkey on the bridge's own page (ADR-CV-106), which only the owner can give.
- Already shaped for upstream: decision record `decisions/ADR-CV-101-…`, README "How it behaves" and tool table, a `# RULE` ("a folder holding or inside a denied folder is never granted") with its row in `scripts/mutations/`, `tests/test_access.py`, mutation run 5/5.
- Expect discussion: listing names of sessions outside the root shares more with the assistant than today (a side chat's name is the owner's own words); the deny list's contents; upstream's "few tools" principle (two new tools). `SessionManager` takes the deny list as a parameter (empty by default) so tests elsewhere don't hit it; the served bridge passes `default_denied()`.
- Known gap: a side chat outside the open folders isn't marked `side_chat` (the mark is only added to full rows). Grouping side chats under their parent session is deferred.

### 6a. Telling a session who reads it — fork "Tell a session who reads its reply when the relay writes to it", "Tell a session to answer a Claude Code sender by direct message"

- What: every message sent with `message_active_session` / `ask_active_session` ends with a short bridge note: who it comes from, that the owner sees neither it nor the reply unless the sender passes them on, how to answer, self-contained. From the voice assistant (`RELAYED_MESSAGE_NOTE`), which has no inbox: answer in the reply text rather than SendMessage. From a Claude Code session on the same computer (`SESSION_MESSAGE_NOTE`, found by the call's tool-call id): answer it by SendMessage to its name, which wakes it, except for a question, which waits for the reply text.
- Why: receiving sessions answered with SendMessage into nothing and wrote to the owner about text only the assistant had seen.
- Expect discussion: the notes use this fork's role names ("the relay"); upstream would need its own wording, and maybe a setting. Every relayed message gets longer by the note, which a session sees in its transcript.
- Tests: `tests/test_relayed.py`.

### 6b. Project list from Claude Code's history — fork "List the folders Claude Code was used in, wherever they are"

- What: `list_folders` (upstream's `list_projects`) lists the folders Claude Code was used in during the last 30 days, anywhere, read from `~/.claude/projects` (the real folder found from the working folder a conversation records), each marked with `access` when not open; deleted folders left out; search on the folder name.
- Why: the root-only list missed busy folders elsewhere and didn't match the desktop app's sessions.
- Depends on 6 (access marks). Test: `test_the_folder_list_includes_folders_claude_was_used_in_anywhere`.

### 6c. peek_session and clearer reader descriptions — fork "Rename the quick question to peek_session and say what each reader sees"

- What: `side_question` renamed `peek_session` (too close to the desktop app's side chats); descriptions of `peek_session`, `digest_session` and `list_sessions` say what each reads (8,000 vs 150,000 characters of the conversation's words, never tool results or files), how fast, and why an idle bridge session isn't "running".
- Expect pushback: renaming a tool breaks clients that remember it. The description part is uncontroversial on its own.

### 6d. The side session, a read-only fork — fork "Ask a read-only fork of a busy session while it keeps working" (ADR-CV-103)

- What: `ask_forked_session(session, question)` forks a running session's conversation through the Agent SDK (`resume` + `fork_session`), one new fork per question, with the owner's settings; a PreToolUse hook refuses every tool outside a reading list (and a matching permission callback), so it changes nothing; returns the answer and tools used.
- Why: keeps a voice conversation going while a session works for a long time, with answers that can draw on the code.
- Follow-up fix, fork "Read past a resumed session's leftover turn, and never call silence an answer": resuming a desktop session first closes a turn left pending in its history (background-task notifications), reported as a result with no turns and no text; the first version took that as the answer. It now skips up to three such turns, and an empty answer or an error result comes back as `status: "error"` with a `detail`. Fold into the same pull request.
- Points to raise: cost per question (each fork carries the whole conversation; the first fork of a desktop session likely misses the prompt cache); forks' conversations stay in Claude Code's history. `# RULE` "the side session never runs a tool that can change anything", `tests/test_fork.py`.

### 6d'. Waits within the caller's time budget — fork "Let the side session work on past the caller's time budget" (ADR-CV-104)

- Upstream has per-client time budgets; what's left to offer: the side session working on past the budget (`still_working` with a way to read on), `next_after` on `message_active_session`, and a log line with its duration for a call the client abandons.
- Why: the Claude app drops a tool call at about 30 s (measured: 28.6 s came back, ~35 s and ~76 s dropped), and the assistant only sees "MCP tool call failed".
- Upstream relevance: high for any claude.ai client; the side-session parts depend on 6d. Tests: `tests/test_cap.py`.

### 6d''. Whether an open session's reply is final — fork "Tell the assistant how to read a reply that may not be final"

- Upstream has `reply_final` itself; what's left is the manual's lines: a reply under `still_working` is the latest whole message so far, maybe already the answer, and how to read on until the session is idle.
- Why: seen live, the whole answer had been written while the turn was still open, and the assistant took the result for a status bug.
- Upstream relevance: medium; with 6e. Test: `tests/test_guide.py`.

### 6d'''. Desktop-app sessions reported as "desktop" — fork "Call a session open in the desktop app a desktop session"

- What: `session_kind()` in `tree.py`, used by `fleet_recap` and `session_tree`: a registry entry with `entrypoint: claude-desktop` is "desktop", otherwise "terminal" for `kind: interactive` and "background" for the rest. `list_active_sessions` still shows the raw registry kind.
- Why: the desktop app registers its sessions as interactive, so they were reported as terminal sessions.
- Upstream relevance: high, small and independent. Tests: `tests/test_tree.py`, `tests/test_fleet.py`.

### 6i. A read starts the turn it has no room for — fork "Start the first turn a read has no room for instead of hiding it"

- What: reading a session's turns forward with a text budget, the first turn that doesn't fit is included anyway, cut to the room left (at least 300 characters) and marked cut with its length; the count of turns left covers only what follows.
- Why: a short turn followed by a long reply came back alone with "two turns left", and the assistant reported nothing new while the reply was there. Depends on the budgeted read ("Fill a text budget when a read asks for no count", "Tell the assistant to read on until no turns are left"). Test in `tests/test_live.py`.

### 6k. Messages to a session marked not destructive — fork "Stop asking the owner before every message to a session", "Tell the assistant to list every session it can see"

- What: `message_active_session` and `ask_active_session` say `destructive_hint=False` ("Stop asking the owner before every message to a session"), then `stop_active_session` too ("Tell the assistant to list every session it can see", the owner's call: a stopped session can be resumed); `tests/test_server.py` names the one tool marked destructive (`request_access`) and holds every other tool that isn't read-only to an explicit False, since MCP counts a tool that says nothing as destructive. Upstream would likely keep its stop tool destructive; offer "Stop asking the owner before every message to a session" alone.
- Why upstream should want it: the Claude app asks before every call to a destructive tool and offers only "Allow once", even with the connector set to always allow (seen 2026-11-05), so every message in a voice conversation waits for a tap on the phone. A message deletes nothing, and the receiving session keeps its own permissions and approvals (upstream's ADR-CV-003).
- Expect pushback: upstream marked both destructive itself (`caee4a0`, `d9faf6c`); a message can make a session do anything, so the author may see the tap as a safeguard. The answer: the receiving session's own permission rules are the safeguard, and a tap on every message makes the voice loop unusable.
- Test first: the new test was seen failing on `message_active_session` before the markers changed. In upstream the tool names differ for the folder tools (6), so the test's destructive set would be upstream's own.

### 6l. Waits that run to the caller's limit, and waiting in silence — fork "Wait for a session's reply by default, and in silence", "Wait to the caller's limit when no length is given", "Wait to the caller's limit on questions too when no length is given"

- What: a wait given no length (`wait_seconds` left out, on `wait_for_session`, `ask_active_session` and the side-session tools) runs to the caller's time budget, counted from the call (a question's delivery included), and the parameter's description says to leave it out; the manual tells the assistant to wait for a reply by default, say nothing between waits, and answer the owner if they speak, letting the call they talked over return by itself.
- Why: Claude's voice mode moves a call past about 10 s to the background, and its result later wakes the assistant with its tools back (tested with waits of 18 to 185 s, 2026-11-11). So one long wait carries the wait loop, where a wait cut short came back "still_working" and woke the assistant only to wait again. The fork first had short defaults (8 s, then 5 s) to stay under the 10 s mark, but the app counts that mark across the turn, so most questions crossed it anyway: none of 29 answered from the app ended under 5 s (2026-11-06 to 11-14).
- Upstream relevance: upstream's 60 s default under its 10 s budget for the Claude app already runs to the limit there; the gain comes with a larger budget for the app (the fork runs with 180 s, under claude.ai's documented 240 s tool-call timeout) and the manual's wording (6e; depends on 6d'). Test: `tests/test_cap.py`, `tests/test_news.py`, `tests/test_guide.py`.

### 6m. Notes on late results — fork "Remind the assistant where a late result comes from", "Tell the assistant a wait on the session is still running"

- What: a middleware (`result_notes()`) adds `late` to the result of any call that took 1 s or more (the app may deliver it late, inside a call carrying `_deferred_result_for`, which only the app writes), and `pending` to a call on a session while a wait or ask on it still runs.
- Why: voice mode delivers a backgrounded result as a call the assistant seems to have written, and assistants copy it to fetch results, refused each time; the note sits in the very result they copy.
- Expect pushback: it names a field of one client's internals in every slow result; upstream may want it behind a setting. It also rewrites the result's text content from its structured content. Tests: `tests/test_cap.py`.

### 6n. The manual handed to a conversation that never read it — fork "Hand the manual to a conversation that never read it"

- What: `result_notes()` remembers, by the `mcp-session-id` header, which MCP sessions have had the manual (the newest 1,000); the first result of a session that never called `guide` carries the manual itself, first in the result. A client without an MCP session (stateless, or the 2026-07-28 single-exchange protocol) gets nothing, so it isn't handed the manual on every call.
- Why upstream should want it: the Claude app opens one MCP session per conversation but doesn't pass server instructions on to the model, and its agents load tools by search, so `guide` is easily never called (seen for a whole evening). Upstream has the same instructions and would face the same.
- Expect pushback: about 3,500 characters of context once per conversation; it depends on the app keeping one MCP session per conversation (seen in this fork's log: about one initialize per conversation, hundreds of calls each), and would deliver nothing if the app moves to a sessionless protocol. Depends on 6e and 6m. Test first: `tests/test_server.py`, over HTTP with an initialize handshake, seen failing before the change.
- Follow-ups, fork "Tell an agent the attached guide spares a call to guide" (a note before the manual, so the agent doesn't call guide for the same text) and "Leave out the note to skip guide on a call to guide itself". Fold both into the same pull request.
- Later, fork "Hand the guide to each Claude Code session, and again after a compaction": a Claude Code session sends no MCP session, so the bridge knows it by the transcript holding the call (`claudecode/toolUseId`, the way `confirm_ask` finds its caller) and hands the manual over on its first call, then again when that transcript shows a compaction (a `compact_boundary` line past what was read last). Only for sessions running on the bridge's own computer, whose transcripts it can read; a claude.ai text chat sends a call id too but its transcript isn't there, so it still gets nothing. Upstream relevance: the same for any Claude Code user; pushback to expect: a read of each running session's transcript tail on every Claude Code call. Test first: `tests/test_guide_sessions.py`, seen failing before the change.

### 6o. request_access by folder path — fork "Open a folder by its path with request_access"

- What: `request_access(session=None, folder=None)`, exactly one; `folder` is a full path, opened with the same grant, deny list and destructive marker as a session's folder. README and ADR-CV-101 updated.
- Why: to start work remotely in a folder no session runs in, which the session-only form could never open. Depends on 6.
- Expect pushback: a path typed by the assistant rather than a folder the owner already works in; the answer is the app's own prompt on the destructive tool, which shows the path and offers no "Always allow". Test first: `tests/test_access.py`, seen failing before the change.

### 6o'. Claude Code's trust with a folder opened by path — fork "Trust a folder in Claude Code when the owner opens it by path"

- What: `request_access(folder=...)` also sets `projects["<path>"].hasTrustDialogAccepted` in Claude Code's config file (`~/.claude.json`, or under `CLAUDE_CONFIG_DIR`), keyed on the git repository's root when inside one, as Claude Code's permissions docs describe for trusting a folder by hand. Other keys are kept and the file is swapped in one step; a failure leaves the grant and says why (`trust_not_set`). A closed repository root is never trusted.
- Why: Claude Code refuses a background session in a folder it never trusted and asks only in a terminal, so a folder opened from afar still couldn't take a session.
- Expect pushback: the bridge writing another program's config file, which Claude Code also rewrites while it runs (a lost update is possible, though rare). Claude Code's own safety check refused writing this change until the owner approved it directly. Depends on 6o. Test first: `tests/test_access.py`.

### 6p. The delegation rules in the manual — fork "Move the bridge's delegation rules from memory into the guide"

- What: the manual says to resume with `attach_conversation`, to give a session a self-contained brief and ask it to propose changes before applying them unless the owner said otherwise, and to confirm work done as agreed in a few words. Moved from the owner's own assistant memory. Fork-specific wording; offer only as part of 6e.

### 6q. Opus when no model is asked for — fork "Start a session on Opus when no model is asked for"

- What: `start_active_session` asks for `opus` when no `model` is given (`DEFAULT_MODEL`); a named model still wins. Test: `tests/test_start.py`.
- Why: Claude Code's own default follows the account (Fable on the owner's), while the owner wants Opus for everyday work.
- Expect pushback: a hard-coded model; upstream would more likely take it as a setting (an env variable, unset by default).

### 6r. "Remote Control" instead of "background" in the session tools — fork "Present the bridge's sessions as on Remote Control, not background"

- What: titles and descriptions of the start, resume and stop tools say the sessions are on Remote Control, not "background"; kept by `tests/test_surface.py`.
- Why: agents told the owner sessions ran "in the background", when they are visible on the phone and in the desktop sidebar.
- Upstream relevance: low; this fork's wording, and upstream's sessions may not be on Remote Control.

### 6s. Waking the sessions a shutdown interrupted — fork "Wake the sessions a shutdown interrupted when the bridge starts", fixed in "Wake what was running at the bridge's last look, not by boot time"

- What: when the served bridge starts, it wakes in place (`claude --resume <id> --bg`, saved options and Remote Control kept) each background session it started or resumed that was running at its last look (the watcher's `live_snapshot`, kept in the database) and that `claude agents --json --all` now lists as failed. Each wake or failure is logged. "Wake the sessions a shutdown interrupted when the bridge starts" used the machine's boot time instead; on Windows with Fast Startup that stays weeks old, so "Wake what was running at the bridge's last look, not by boot time" replaced it.
- Why: a shutdown stops every background session and nothing restarts them; the owner could only get one back by asking an agent. The bridge starts at logon, so it can.
- Expect pushback: it starts processes nobody asked for at that moment; upstream may want it behind a setting. Relies on Claude Code marking an interrupted session failed (documented in `agent-view`). Test first: `tests/test_wake.py`.

### 6t. Renaming a session the bridge started in Claude Code itself — fork "Rename a session the bridge started", "Write a new name in the compact form Claude Code uses", "Rename a session through its job record, where Claude Code looks", "Rename a session the bridge started in Claude Code itself", "Rename a busy session in Claude Code once it is idle"

- Upstream has its own `rename_session`, for any running session, renamed in Claude Code by typing into tmux. What's left to offer: the job record as a way to rename a background session in Claude Code, on any platform, beside tmux.
- What: for a session the bridge started, `rename_session` also writes the new name into Claude Code's job record (`~/.claude/jobs/<id>/state.json`: `name`, `nameSource: "user"`, and the `--name` in `respawnFlags`); a running session watches that record and renames itself within seconds, and a wake keeps it. Never mid-turn: a name given then is kept in the bridge's database, the last one given, and the watcher writes it once the session is idle, after a stop or a bridge restart too (`claude_code: pending`); a name set in Claude Code meanwhile wins. Offer as one pull request in the last commit's shape.
- Why: the assistant names a session at start and could never change its mind.
- Expect pushback: it writes Claude Code's internal state, an undocumented format found by reading Claude Code's code (`jobStateNameSync`), which may change with any release. Tests: `tests/test_rename.py`.

### 6g. Session names that keep up with the work — fork "Keep session names current and mark the ones an agent chose", "Keep the name check's hour across bridge restarts"

- What: a name an agent gives (`rename_session` or `start_active_session` without `owner_chose`) is put in curly braces; a name without them is the owner's, unless Claude Code made it itself (`nameSource` "auto"), and only `owner_chose` renames it; others propose one with `propose_session_name`, which shows on the session's row and once in each relay's news. About once an hour the note after a relayed message asks the session whether its name still fits; the hour is kept in the store, so a restart doesn't start it over. Name lookup ignores the braces.
- Why: names drift from what sessions do, and someone who remembers a name finds it gone.
- Expect pushback: braces inside the name are a convention, and a new tool; upstream may prefer a field on the session. Builds on 6t. Tests: `tests/test_name_upkeep.py`, the news one in `tests/test_news.py`.

### 6u. A news line on every result, replacing whats_new — fork "Tell the relay in every result what it hasn't read yet", "Remove whats_new now that every result carries the news"

- What: each tool result for an MCP session can start with `news`, one line naming the sessions that conversation started, resumed, messaged or read that have turns past its read marker (a relayed message never counts; turns the owner typed are counted as such), with their state (finished, still working, waiting, ended), and any session waiting on a permission prompt. Markers live in memory, per `mcp-session-id`. `whats_new` and its cursor are removed ("Remove whats_new now that every result carries the news"); the watcher stays for the wake at start (6s).
- Why: a polling tool nobody called; the relay kept missing replies, most of all when the owner wrote to a session directly.
- Later fix ("Keep a reply as news until the relay reads it, even after another send"): only a read marks a reply read, not a send, and a reply followed by a later message counts as ended even while the session works again. Seen live: the relay sent several messages without reading in between, a report ended between two of them, and every send said "nothing new".
- Later rework ("Follow a session from a message until its answer is read"): the line names only the sessions the relay follows, from its message or question (or `follow_session`, for one the owner hands over) until it has read the answer; reading a session no longer adds it, and what the owner typed in a session moved into the result of the next send to it. A set of every session touched went stale.
- Later fix ("Refuse following from a Claude Code session"): `follow_session`, and `wait_for_session` with no session named, were refused for a call carrying Claude Code's tool-call id, since the list they used was the voice relay's and a session briefing others would fill it. Replaced by the next.
- Later rework ("Follow sessions per relay handle"): each relay has its own follows, read marks and news under a handle, a code such as "@kQzw" the bridge hands out with the first call that follows a session without one, or with one it doesn't know, which goes ahead under it; a `handle` parameter on the tools that follow or read and on `list_active_sessions`, which lists the handles in use. A Claude Code session's handle is its own name. Why: the owner runs several voice conversations at once, all over one connection, and each heard of replies meant for the others.
- Later rework ("Keep a session in a quiet follow once its reply is read"): once its reply is read, a session stays followed quietly, named only when a new finished turn comes that the relay hasn't read, with who started it; a turn from the owner's typing or another session was missed once following had ended. A message the bridge's channel delivers counts as a turn too ("Count a message the relay channel delivers as a turn"): dropped with the other meta lines, it went unseen in reads and its turn was credited to an earlier message. Sessions that stay quiet never show, which kept the old list of every session touched from being noise.
- Expect pushback: an extra field on every result, and a removed tool; depends on 6n's per-conversation identity and on transcript `origin` entries. Test first: `tests/test_news.py`, over HTTP.

### 6v. A finished turn's last message whole — fork "Return a finished turn's last message whole"

- What: once a turn has ended, `wait_for_session`, `ask_active_session` and `read_session_output` return its last message whole up to `FINAL_CHARS` (12,000), with `reply_cut`/`reply_length` (or `cut`/`length`) past that; other turns keep `max_chars`.
- Why: final reports of 5,000 to 6,000 characters needed two or three more reads. Small and independent; offer early. Test first: `tests/test_final_reply.py`.
- Later fix ("Give a read the whole reply, past its page"): a read's page could fill before the reply (a long relayed message took it), leaving the reply cut for another read the relay skipped. A read now returns `reply`, `reply_final` and `reply_index` the way a wait does, from one shared function, whole even past the page; a relayed message shows in a read as one line, starting with its own words rather than the courier's or the channel's wrapper ("Start a relayed message's one line with its own words"); and while part of a reply is unread, the result's next step says to read the rest.

### 6w. Briefs written by the session that holds the context — fork "Have the session that holds the context write a new brief"

- What: one line in the manual's relay scope. Fork-specific wording; only with 6e.

### 6x. Starting sessions in the background on Remote Control — fork "Start new work as background sessions, a window only when asked", "Resume earlier conversations as background sessions", "Continue a former background session instead of copying it", "Leave a conversation that is still running as it is when attached"

- Status: an idea for later (owner); nothing prepared, no branch.
- What: a session the bridge starts, or resumes from an earlier conversation, runs as an ordinary Claude Code background session with Remote Control on, with no window; the owner opens it from the phone or the desktop app.
- Why upstream should want it: upstream's own commit `248f405` (2026-11-02) opens a terminal window for the same reason, sessions the phone couldn't reach. The background way needs no desktop, no terminal program and no tmux, and works the same on Windows, macOS and Linux.
- Expect pushback: upstream just built the window, with settings and a companion-app page for it. Offer it as one more launch mode (`launch = "remote_control"` or similar) next to theirs, not as a replacement. Build it on upstream's launch settings.
- Test first: the fork's start tests (`tests/test_start.py`), rewritten against upstream's launch settings.

### 6z'. Why a session left the list, and how to bring it back — fork "Say when Claude Code closed a session for low memory", "Resume a session Claude Code closed for low memory when messaged", "Tell the relay to resume a session gone from the list"

- Status: an idea; nothing prepared, no branch.
- What: Claude Code's background manager closes idle background sessions when memory runs low (its `daemon.log`: "bg retire <id>: ... [low memory]"). The bridge reads that log: a followed session that ended this way says so in the news, with "attach_conversation resumes it"; and a message to a session that isn't running says whether Claude Code closed it, and gives the id to resume it with. A message or a question to a session closed this way resumes it in place first. The guide and attach_conversation's description say plainly that a gone session is resumed rather than started afresh.
- Why upstream should want it: without it, sessions drop out of the list with no word, and relays don't think of resuming them.
- Expect pushback: it reads a log file whose format is Claude Code's, not a documented interface. Nothing breaks if it changes: the reason is just left out.
- Test first: `tests/test_news.py` and `tests/test_courier.py`, the tests named after the rule.

### 6z''. A session compacted at a break it chooses — fork "Compact a session's conversation after the turn that asks", "Tell a session why its compaction was given up", "Log a session woken from a compaction without Remote Control"

- Status: an idea; nothing prepared, no branch.
- What: a tool, `compact_after_turn`, that a background session the bridge started calls on itself, with optional instructions for the summary. Once the calling turn has ended (the session seen at rest twice in a row), the bridge stops the session, runs `claude -p "/compact <instructions>" --resume <id>` in its folder, and wakes it in place; a failed compaction still wakes it. If the turn never seems to end within 30 minutes, the bridge drops the request, logs the state it last saw and the background tasks it counted, and tells the session why. The stop archives a Remote Control session in the Claude app and the wake un-archives it, so the session is off the app's list for the length of the compaction; a session woken without its Remote Control link (claude.ai refuses to un-archive it on a device it doesn't trust) is logged. Other sessions are refused and told to have `/compact` typed.
- Why upstream should want it: a session driven by voice has no one at the keyboard to type `/compact`, and Claude Code's own automatic compaction fires at a size threshold, mid-task. This lets the session compact at a natural break, after writing down what must survive.
- Expect pushback: it stops and restarts a session, which reloads its connectors; and it relies on `/compact` working in print mode on a resumed conversation, which Claude Code supports (its `/compact` is marked as working non-interactively) but doesn't document as a way to compact another session.
- Test first: `tests/test_compact.py`.

### 6z'''. A session that started without its claude.ai connectors, restarted — fork "Restart a session that started without its claude.ai connectors", "Wake interrupted sessions once claude.ai answers"

- Status: an idea; nothing prepared, no branch.
- What: Claude Code writes one log file per MCP server each time a session's process starts (`claude-cli-nodejs/Cache/<project>/mcp-logs-<server>/<start time>.jsonl`, naming the session on each line). At each look, the bridge checks each running session's latest start: one with logs for other servers but none for a `claude-ai-*` connector, 90 seconds on, in a folder where connectors have loaded before, came without them. A background session the bridge started is then stopped and resumed in place once at rest and once claude.ai's connector proxy answers, once per start. A session busy for 10 minutes is told it will be restarted at rest; one the bridge didn't start, or whose restart didn't bring them back, is told to warn the owner. Module `connectors.py`. The bridge's own wake-up of interrupted sessions at its start waits for the proxy to answer too, up to 5 minutes.
- Why upstream should want it: after a sleep, Claude Code's daemon can respawn background sessions before the network is back; they then run without any claude.ai connector, and nothing says so until a tool is missed.
- Expect pushback: it reads Claude Code's own log files, not a documented interface (anything unexpected tells nothing, and nothing is done); the cache folder's place is the Windows one (`%LOCALAPPDATA%`), so other systems need theirs; and upstream runs sessions in tmux, where the respawn may not happen at all.
- Test first: `tests/test_connectors.py` and `tests/test_wake.py`, the tests named after the rules.

### 6y. Updating the bridge on Windows — fork "Update the bridge on Windows, where there is no systemd"

- What: `update_bridge` works without systemd. On Windows the bridge starts the updater as a detached process, and the updater restarts the bridge by ending it with exit code 75 (`RESTART_EXIT`), which the script that runs the bridge takes as "start me again"; a rollback does the same. The updater also checks health on the address the bridge listens on (`--host`), not always 127.0.0.1, which helps a Linux bridge bound to a Tailscale or WireGuard address too.
- Why: upstream's update relies on `systemd-run` and `systemctl`, so on Windows (and macOS) it fails at the start.
- Expect pushback: the restart needs the launcher's cooperation (a loop on exit code 75), which upstream doesn't ship for Windows; it comes with 8 (Windows in the README and CI). Tests: `tests/test_update.py`, the three Windows tests.

### 6z. Trusting a new folder on Windows — fork "Trust a new folder under the name Claude Code looks it up by"

- What: `trust_folder`, which marks a folder `start_active_session` has just made as trusted in Claude Code's config, writes its key with forward slashes (`folder.as_posix()`), as Claude Code keys a Windows folder; with the backslashed `str(folder)`, Claude Code never read it and refused the start ("Workspace not trusted"). Found in the resync's trial run.
- Why: a small bug fix on Windows; no change on Linux or macOS, where both forms are the same. Offer with 8, or alone. Test first: `tests/test_start.py`, with a Windows path, so it fails on Linux CI too.

### 6e. A manual for the assistant — fork "Give the bridge a manual, sent at connection and kept in a guide tool", cut down in "Keep in the manual only the rules that span tools"

- What: the server instructions rewritten as a short manual (roles, the rule that the owner sees nothing passing through tools, keeping agents' words apart, the relay's scope, how to wait, what only the owner answers), and a `guide` tool returning the same text for clients that drop server instructions. Since "Keep in the manual only the rules that span tools" it holds only what spans tools; what one tool needs is in that tool's description (6h).
- Expect discussion: the manual is written around this fork's voice relay naming; upstream would take the structure and the guide tool, and keep its own words. Test: `tests/test_guide.py`.

### 6f. "List every session" instruction and the relay label — fork "Tell the assistant to list every session it can see"

- What: one paragraph telling the assistant to name every session from every source when asked. Folded into 6e's manual; only worth offering as part of it.

### 13. Background steps that log their failures — fork "Log a failed background step once instead of dropping it"

- What: each step of the bridge's housekeeping loop (the look, other machines, the approval pick-up, the prompt sweep, the work board, and the fork's own steps) runs in `background_step`, which logs a failure once and again only when it changes or comes back after a recovery; the warm-up of the side-question reader logs its failure through a done-callback instead of dropping it.
- Why: upstream's loop wraps each step in `contextlib.suppress(Exception)`, so a step that breaks for good leaves no trace, against upstream's own rule that an error names what is wrong and the next step.
- Already shaped for upstream: two `# RULE` markers with their rows in `scripts/mutations/`, `tests/test_background_steps.py`. The fork's version also covers its logon wake-up; upstream's has none, so that part stays out of a pull request.
- Expect discussion: the log level (warning) and whether to log every repeat instead of once.

### 7. Serving under a path prefix — not in the fork yet

- What: the bridge works behind a reverse proxy under a path (`CLAUDE_VOICE_PUBLIC_URL=https://host/claude-voice`) only because Nginx also forwards three root paths. One of them is the bridge's own doing: the consent form posts to an absolute `/oauth/consent` (`src/claude_voice/oauth.py`, `_consent_form`). A relative action (`action='consent'`) would remove it. The other two are where OAuth clients look for metadata (RFC 9728, RFC 8414) and can't be avoided by the bridge alone; document them.
- Why: upstream documents Tailscale Funnel only; a prefix behind an existing reverse proxy is a common setup.
- Would need: the change, a test that the form's action stays under the public URL, and a README section on running behind a reverse proxy.

### 8. Windows in the README and CI — prerequisite for the Windows ones (6y, 6z)

- Upstream says "Linux or macOS". Supporting Windows means a decision record (scope: the bridge runs on Windows; the systemd service and `install.sh` stay Linux-only; Windows runs it by hand or as a startup task), a README section, and a Windows job in `.github/workflows/checks.yml` so it stays working (today every job is `ubuntu-latest`).
- Without the CI job, Windows support would rot; upstream's own rule ("every rule is enforced by something that can fail") argues for it.
- Best offered first as an issue, asking whether Windows is wanted at all, before any pull request.

### 10. The owner's confirmation with a passkey — fork "Describe the owner's confirmation through the bridge", "Settle who may confirm and how exactly the words must match", "Pin the confirmation to the running commit", "Let the owner confirm a session's request from a voice chat", "Confirm with the owner's passkey on the bridge's page" (ADR-CV-105, ADR-CV-106)

- What: a session calls `confirm_ask` with the exact summary of what it wants approved and gets a short code; the relay calls `confirm_with_owner` with the code and sends the owner the link it returns; the owner opens the bridge's own page, sees the request as the bridge stored it, and confirms with a passkey (fingerprint, face or PIN), whose signature the bridge checks; the session then calls `confirm_check` and acts only on "confirmed" with its own summary. `claude-voice passkey add` registers the passkey once, from a one-time link. Design, what each party can observe and the limits: `docs/owner-confirmation.md`.
- Why upstream should want it: a session rightly refuses a relayed approval, since a message from the relay looks like any other session's; so far the owner had to type "OK" at the computer. The confirmation travels by a path no model can produce, and the session checks it itself. The Claude app's approval card can't carry it: a setting skips the card, and the bridge can't see whether it was shown.
- Expect pushback: a large feature, three tools and a command; a new dependency (`webauthn`); a page served by the bridge, which answers only from a private network (the owner's VPN, the computer itself, or a tailnet), so it needs one reachable from the owner's phone. `request_access` uses the same passkey in the fork (6), so the two may be offered together, or this one first with its own tools.
- Test first: `tests/test_confirm.py`, `tests/test_passkeys.py`.

### 11. A suite that keeps to its own checkout, and away from the owner's Claude Code — fork "Leave a nested checkout's pages to its own run", "Keep the owner's own Claude Code out of the suite"

- Status: an idea; nothing prepared, no branch.
- What: `tests/test_documented_commands.py` skips a folder holding its own `.git` (a Claude Code worktree under `.claude/worktrees`, a clone) and prunes skipped folders instead of walking `.venv`; `build_server` looks up `jobs.background_sessions`, `jobs.background_jobs` and `transcripts.summarize` when it is built, not when `server.py` loads, and `tests/conftest.py` puts fakes there, as it already does for GitHub and the side-question reader.
- Why upstream should want it: anyone whose Claude Code sessions use worktrees runs each documented command once per worktree, and fails on another branch's pages. Every watched bridge in the suite ran the developer's real `claude agents` listing, and the `digest_session` case of `tests/test_api.py` made a real Haiku call on their account in every run (no login in CI, so it fails quietly there).
- Expect pushback: little; three defaults become `None`, as `side_answer` already is.
- Test first: `tests/test_documented_commands.py` (a nested checkout keeps its own pages), `tests/test_wake.py` and `tests/test_digest.py` (a bridge given no helper uses the one in place when built).

### 12. A quick ratchet check that skips the sweep on both sides — fork "Leave the mutation sweep out of a quick check's copy of main too"

- Status: an idea; nothing prepared, no branch.
- What: with no kept levels for main, `ratchet.py check --skip-mutation` measures main in a copy; the copy now gets `--skip-mutation` too, measures no mutation, and the check compares none.
- Why upstream should want it: `CONTRIBUTING.md` offers `--skip-mutation` as the quick look, but locally main's levels are never kept, so the copy ran the whole sweep anyway: about 30 minutes here, most of a 40-minute "quick" check.
- Expect pushback: a quick check then says nothing about mutation; continuous integration still runs the full sweep.
- Test first: `tests/test_ratchet.py`, the test named after the rule "a quick check measures no mutation on either side".

### 13. The running commit read once per process — fork "Ask git for the running commit once per process"

- Status: an idea; nothing prepared, no branch.
- What: `metrics.version()` and `metrics.uncommitted()` are cached for the process.
- Why upstream should want it: both describe the code the process loaded, which a later pull doesn't change until a restart; the suite builds hundreds of bridges, each asking git twice.
- Expect pushback: small; a test that fakes git must clear the cache (`tests/test_agent.py` has a fixture for it).
- Test first: `tests/test_agent.py`, the test that counts the git calls.

### 14. The suite on parallel workers — only in the fork's `CLAUDE.md` (fork "Run the suite on eight parallel workers here")

- Status: an idea; the fork changed no project file for it.
- What: `pytest-xdist` in the dev dependencies and `-n auto` (or a fixed count) in the gates and CI. Here, 8 workers took the suite from 182 s to 37 s without coverage, green on every try; 12 or 16 workers were no faster.
- Why upstream should want it: the suite is about 1,300 tests and grows fast; one process uses one core.
- Expect pushback: timing tests ("answers within two seconds") get less room on a loaded runner; a new dependency. Offer it as an issue first.

## Not for upstream

- The owner's setup around the fork (Windows service scripts, the server proxy, its values and working notes), kept in a private repository: specific to that setup.
- The fork's `CLAUDE.md` (fork "Check how far behind upstream the fork is at each session start"): each session fetches upstream and offers the owner a resync when the fork is behind, the last sync being the merge base of `main` and `upstream/main`. It sits after upstream's own pointers, under "The owner's fork". The same section (fork "Say the fork is AI-managed") says the fork is AI-managed, maintained by kelno's own agents under kelno's private rules, and that for those agents the gates run as slow checks rather than before every push, with no ratchet on the fork's own `main` and the suite on 8 parallel workers (14).
- The guide's line saying the bridge is the owner's own software, so a change to it is a fair suggestion when a tool gets in the way (fork "Tell the relay the bridge is the owner's own project", then "Call the change feedback names a lead, not the fix"): true only of this fork.
- The commit-msg hook calls `python3`, which the python.org installer doesn't create on Windows (only `python.exe`), so the hook fails there unless something else provides `python3`. Fixed on this PC outside the repo, with a `python3.exe` link. Worth a one-line issue at most, not a fork change.

## Merged upstream

Our pull requests upstream merged, newest first.

- [#129](https://github.com/roykollensvendsen/claude-voice/pull/129), every mutation row checked to compile (2026-11-10, `f12686d`): a row whose change leaves the code unable to load is reported as broken, and the suite applies every row and requires the result to compile, so a broken row shows on any run, not only in the mutation job.
- [#109](https://github.com/roykollensvendsen/claude-voice/pull/109), `wait_for_session` (2026-11-10, `08fe40c`): a caller waits for a session's turn to end, or for it to need its user, without sending anything. Three rounds of review; the fork's version goes further without `after` (`UPSTREAM.md`, "Same tools, different behaviour").
- [#108](https://github.com/roykollensvendsen/claude-voice/pull/108), each tool says what it is for in its first line (2026-11-10, `a3fa2ce`): every description opens with one whole sentence of at most 78 characters, since the Claude app shows only that much until a tool is loaded. Upstream keeps its own words for the lines; the author plans a follow-up for the connector-name prefix the app later put inside that cut.
