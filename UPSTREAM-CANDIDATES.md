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
- Follow-up, fork "Rename request_access and revoke_access to open_folder and close_folder": `request_access` and `revoke_access` renamed after what they do, as their titles and the `folder_not_open` status already said. Upstream has neither tool, so it would take the new names from the start.
- What: every running session is listed; one outside the root and the granted folders shows only name, folder, status, kind and `access` (`folder_not_open` or `folder_closed`), and can't be read or messaged. `open_folder(session)` grants that session's working folder and everything under it, stored for good; `close_folder(folder)` closes it; `health` lists grants. A built-in deny list (home `.ssh`, `.gnupg`, `.claude`, `.config/claude-voice`, `AppData`, and system folders) is never opened, nor any folder holding one, even inside the root.
- Why: an owner's sessions aren't all under one folder, and the assistant couldn't even tell a session existed. The approval comes from the owner's passkey on the bridge's own page (ADR-CV-106), which only the owner can give.
- Already shaped for upstream: decision record `decisions/ADR-CV-101-…`, README "How it behaves" and tool table, a `# RULE` ("a folder holding or inside a denied folder is never granted") with its row in `scripts/mutations/`, `tests/test_access.py`, mutation run 5/5. The module is `folder_access.py` here: upstream's `access.py`, since 2026-11-22, is the hub's phone permissions.
- Expect discussion: listing names of sessions outside the root shares more with the assistant than today (a side chat's name is the owner's own words); the deny list's contents; upstream's "few tools" principle (two new tools). `SessionManager` takes the deny list as a parameter (empty by default) so tests elsewhere don't hit it; the served bridge passes `default_denied()`.
- Known gap: a side chat outside the open folders isn't marked `side_chat` (the mark is only added to full rows). Grouping side chats under their parent session is deferred.

### 6a. Telling a session who reads it — fork "Tell a session who reads its reply when the relay writes to it", "Tell a session to answer a Claude Code sender by direct message"

- What: every message sent with `message_active_session` / `ask_active_session` ends with a short bridge note: who it comes from, that the owner sees neither it nor the reply unless the sender passes them on, how to answer, self-contained. From the voice assistant (`RELAYED_MESSAGE_NOTE`), which has no inbox: answer in the reply text rather than SendMessage. From a Claude Code session on the same computer (`SESSION_MESSAGE_NOTE`, found by the call's tool-call id): answer it by SendMessage to its name, which wakes it, except for a question, which waits for the reply text.
- Why: receiving sessions answered with SendMessage into nothing and wrote to the owner about text only the assistant had seen.
- Expect discussion: the notes use this fork's role names ("the relay"); upstream would need its own wording, and maybe a setting. Every relayed message gets longer by the note, which a session sees in its transcript.
- Tests: `tests/test_relayed.py`.

### 6a'. Messages carried in through a Claude Code channel — fork "Add a channel that carries the relay's messages into a session", "Deliver the relay's messages through a session's channel", "Name the channel's tag as the session sees it", "Pass over a channel whose session was started without channels", "Count a message the relay channel delivers as a turn", "Wait for a message queued behind a busy session's turn to land", "Call only the bridge's own channel \"another relay\""

- Status: a candidate (owner, 2026-11-14); nothing prepared, no branch. Tried on Windows only.
- What: each session the bridge starts runs a small MCP server from a plugin in the repository (`plugin/`, `channel.py`, standard library only), started with `--channels` and the plugin enabled for that session alone. The server declares Claude Code's `claude/channel` capability, connects to a hub in the bridge on `127.0.0.1` with a secret the bridge writes at each start, and turns each message into a channel event, which starts a turn in an idle session. The hub finds a channel's session from its parent Claude Code process. A message goes through the channel when it is connected and the session's process carries it in `--channels`; otherwise, for upstream, through its courier, which would stay for every other session (desktop sessions, sessions started by hand); the fork itself leaves the courier unused and sends to such a session nothing. A message the channel took but never confirmed is reported, not sent again. Claude Code holds a channel message to a busy session until its turn ends, so the bridge counts a session's answer from where the message lands, not from the send. Results say which way a message went, and `health` counts the connected channels. Setup and design: `docs/channel.md`.
- Why upstream should want it: the courier puts a model session between the bridge and every message, at a few seconds each (4 to 8 seconds measured here) and some tokens; through a channel the hand-over takes a fraction of a second, with no model in between. The channel's standing instructions also tell the session that a relayed message is never the owner's own word or approval.
- Expect pushback, and it is real: it rests on Claude Code's channels, a research preview that may change or go away. `--channels` loads only plugins on the channel allowlist, and Claude Code skips a development channel in a background session, so an account without an organization has to allow the plugin through a managed policy on the computer: a user-level policy in the Windows registry here, typed once by the owner; other systems have their own managed settings, not tried. The plugin runs in place from the clone with `uv`, so the clone can't move, and the setup is four commands the user types once. Upstream may prefer to wait until channels leave the preview, or keep the channel as an opt-in beside the courier.
- Test first: `tests/test_channel.py`, `tests/test_channel_hub.py` and `tests/test_channel_delivery.py`; the turn it starts, `test_a_message_the_relay_channel_delivers_is_kept_as_a_turn` in `tests/test_raw_transcript.py`; a message held until a busy session's turn ends, `tests/test_queued_messages.py`.

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

- What: `message_active_session` and `ask_active_session` say `destructive_hint=False` ("Stop asking the owner before every message to a session"), then `stop_active_session` too ("Tell the assistant to list every session it can see", the owner's call: a stopped session can be resumed); `tests/test_server.py` names the one tool marked destructive (`open_folder`) and holds every other tool that isn't read-only to an explicit False, since MCP counts a tool that says nothing as destructive. Upstream would likely keep its stop tool destructive; offer "Stop asking the owner before every message to a session" alone.
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

### 6o. open_folder by folder path — fork "Open a folder by its path with request_access"

- What: `open_folder(session=None, folder=None)`, exactly one; `folder` is a full path, opened with the same grant, deny list and destructive marker as a session's folder. README and ADR-CV-101 updated.
- Why: to start work remotely in a folder no session runs in, which the session-only form could never open. Depends on 6.
- Expect pushback: a path typed by the assistant rather than a folder the owner already works in; the answer is the app's own prompt on the destructive tool, which shows the path and offers no "Always allow". Test first: `tests/test_access.py`, seen failing before the change.

### 6o'. Claude Code's trust with a folder opened by path — fork "Trust a folder in Claude Code when the owner opens it by path"

- What: `open_folder(folder=...)` also sets `projects["<path>"].hasTrustDialogAccepted` in Claude Code's config file (`~/.claude.json`, or under `CLAUDE_CONFIG_DIR`), keyed on the git repository's root when inside one, as Claude Code's permissions docs describe for trusting a folder by hand. Other keys are kept and the file is swapped in one step; a failure leaves the grant and says why (`trust_not_set`). A closed repository root is never trusted.
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

### 6s. Waking the sessions a shutdown interrupted — fork "Wake the sessions a shutdown interrupted when the bridge starts", fixed in "Wake what was running at the bridge's last look, not by boot time", "Resume the sessions named to wake at start at every bridge start"

- What: when the served bridge starts, it wakes in place (`claude --resume <id> --bg`, saved options and Remote Control kept) each background session that was running at its last look, the owner's own too (ADR-CV-108) (the watcher's `live_snapshot`, kept in the database) and that `claude agents --json --all` now lists as failed. Each wake or failure is logged. "Wake the sessions a shutdown interrupted when the bridge starts" used the machine's boot time instead; on Windows with Fast Startup that stays weeks old, so "Wake what was running at the bridge's last look, not by boot time" replaced it. Since "Resume the sessions named to wake at start at every bridge start", a top-level `wake_at_start` in `config.toml` lists sessions, by name or id, that the bridge resumes at every start when they aren't running, whether its last look saw them or not: at a shutdown Windows may end a session a moment before the bridge, which then misses it. Since "Leave a parked session down when the bridge starts after a restart", a session `stop_active_session` had parked (6z'''') and that hasn't written to its conversation since is left down.
- Why: a shutdown stops every background session and nothing restarts them; the owner could only get one back by asking an agent. The bridge starts at logon, so it can.
- Expect pushback: it starts processes nobody asked for at that moment; upstream may want it behind a setting. Relies on Claude Code marking an interrupted session failed (documented in `agent-view`). Test first: `tests/test_wake.py`, `tests/test_wake_at_start.py`.

### 6t. Renaming a session the bridge started in Claude Code itself — fork "Rename a session the bridge started", "Write a new name in the compact form Claude Code uses", "Rename a session through its job record, where Claude Code looks", "Rename a session the bridge started in Claude Code itself", "Rename a busy session in Claude Code once it is idle"

- Upstream has its own `rename_session`, for any running session, renamed in Claude Code by typing into tmux. What's left to offer: the job record as a way to rename a background session in Claude Code, on any platform, beside tmux.
- What: for a background session, `rename_session` also writes the new name into Claude Code's job record (`~/.claude/jobs/<id>/state.json`: `name`, `nameSource: "user"`, and the `--name` in `respawnFlags`); a running session watches that record and renames itself within seconds, and a wake keeps it. Never mid-turn: a name given then is kept in the bridge's database, the last one given, and the watcher writes it once the session is idle, after a stop or a bridge restart too (`claude_code: pending`); a name set in Claude Code meanwhile wins. Offer as one pull request in the last commit's shape.
- Why: the assistant names a session at start and could never change its mind.
- Expect pushback: it writes Claude Code's internal state, an undocumented format found by reading Claude Code's code (`jobStateNameSync`), which may change with any release. Tests: `tests/test_rename.py`.

### 6g. Session names that show which are the owner's — fork "Keep session names current and mark the ones an agent chose", "Remove the bridge's hourly name check", "Protect the session names the owner marks with an asterisk", "Hold the owner's asterisk at a resume, a status and a lookup"

- What: a name with an asterisk anywhere in it is the owner's, and only `owner_chose` renames it or gives a name an asterisk (`rename_session`, `start_active_session`, `attach_conversation`); others propose one with `propose_session_name`, which shows on the session's row and once in each relay's news. Name lookup ignores the asterisk. Claude Code's `nameSource` can't tell a title given at launch from a name the owner gave, so the mark lives in the name itself.
- Why: names drift from what sessions do, and someone who remembers a name finds it gone.
- Expect pushback: an asterisk inside the name is a convention, and a new tool; upstream may prefer a field on the session. Builds on 6t. Tests: `tests/test_name_upkeep.py`, the news one in `tests/test_news.py`.

### 6u. A news line on every result, replacing whats_new — fork "Tell the relay in every result what it hasn't read yet", "Remove whats_new now that every result carries the news", "Keep a reply as news until the relay reads it, even after another send", "Follow a session from a message until its answer is read", "Follow sessions per relay handle", "Keep a session in a quiet follow once its reply is read", "Count a message the relay channel delivers as a turn", "Give a chat without an MCP session a handle and its news", "Refuse to stop a session whose reply a relay hasn't read", "Don't hold a stop for a reply sent to a Claude Code session"

- What: each tool result can start with `news`, one line on the sessions the relay follows and any session waiting on a permission prompt, and end with `next_step`, what follows for the relay in the states where something does. A message or a question follows a session, and so does `follow_session` for one the owner hands over, until the relay has read the turn that answers it; only a read marks a reply read, not a send. Then the session stays in a quiet follow: named only when another turn of it ends that the relay hasn't read, with who started it (the owner typing, another session, another relay), until stopped, until it ends, or after a day with nothing new. Each relay keeps its follows and read marks under a handle, a code such as "@kQzw" the bridge hands out with its first follow (`handle` on the tools that follow or read; a call without one, or with one not in use, is told the handles in use); a Claude Code session's handle is its own name. Kept in the bridge's database, over a restart. `whats_new` and its cursor are removed; the watcher stays for the wake at start (6s).
- Why: a polling tool nobody called, and the relay kept missing replies, most of all when the owner wrote to a session directly. Several voice conversations use the bridge at once, and without handles each heard of replies meant for the others: a voice call's MCP session id changes at every bridge restart, and the same chat continued by typing carries none, so nothing a call carries identifies its conversation for long.
- Expect pushback: an extra field on every result, a removed tool, and a handle the relay must carry; depends on transcript `origin` entries. Test first: `tests/test_news.py`, over HTTP.

### 6v. A finished turn's last message whole — fork "Return a finished turn's last message whole"

- What: once a turn has ended, `wait_for_session`, `ask_active_session` and `read_session_output` return its last message whole up to `FINAL_CHARS` (12,000), with `reply_cut`/`reply_length` (or `cut`/`length`) past that; other turns keep `max_chars`.
- Why: final reports of 5,000 to 6,000 characters needed two or three more reads. Small and independent; offer early. Test first: `tests/test_final_reply.py`.
- Later fix ("Give a read the whole reply, past its page"): a read's page could fill before the reply (a long relayed message took it), leaving the reply cut for another read the relay skipped. A read now returns `reply`, `reply_final` and `reply_index` the way a wait does, from one shared function, whole even past the page; a relayed message shows in a read as one line, starting with its own words rather than the courier's or the channel's wrapper ("Start a relayed message's one line with its own words"); and while part of a reply is unread, the result's next step says to read the rest.

### 6w. Briefs written by the session that holds the context — fork "Have the session that holds the context write a new brief"

- What: one line in the manual's relay scope. Fork-specific wording; only with 6e.

### 6x. Starting sessions in the background on Remote Control — fork "Start new work as background sessions, a window only when asked", "Resume earlier conversations as background sessions", "Continue a former background session instead of copying it", "Leave a conversation that is still running as it is when attached", "Wake a former background session through a command of its own"

- Status: an idea for later (owner); nothing prepared, no branch.
- What: a session the bridge starts, or resumes from an earlier conversation, runs as an ordinary Claude Code background session with Remote Control on, with no window; the owner opens it from the phone or the desktop app. A conversation that was a background session before is woken in place by `jobs.wake_background` (`claude --resume <id> --bg`, nothing else, since any other flag makes Claude Code start a copy), a command of its own in the fork's `jobs.py` rather than one more switch on upstream's `start_background`.
- Why upstream should want it: upstream's own commit `248f405` (2026-11-02) opens a terminal window for the same reason, sessions the phone couldn't reach. The background way needs no desktop, no terminal program and no tmux, and works the same on Windows, macOS and Linux.
- Expect pushback: upstream just built the window, with settings and a companion-app page for it. Offer it as one more launch mode (`launch = "remote_control"` or similar) next to theirs, not as a replacement. Build it on upstream's launch settings.
- Test first: the fork's start tests (`tests/test_start.py`), rewritten against upstream's launch settings.

### 6z'. Why a session left the list, and how to bring it back — fork "Say when Claude Code closed a session for low memory", "Resume a session Claude Code closed for low memory when messaged", "Tell the relay to resume a session gone from the list", "Resume a session Claude Code closed while idle, not only for low memory", "List the sessions the Claude app shows offline", "Find a closed session by its name the way a running one is found"

- Status: an idea; nothing prepared, no branch.
- What: Claude Code's background manager closes idle background sessions on its own, hours after they last worked (8 hours with Remote Control) or sooner when memory runs low (its `daemon.log`: "bg retire <id>: ...", with "[low memory]" in the second case). The bridge reads that log: a followed session that ended this way says so in the news, with "attach_conversation resumes it"; and a message to a session that isn't running says whether Claude Code closed it, and gives the id to resume it with. A message or a question to a session closed this way resumes it in place first. The guide and attach_conversation's description say plainly that a gone session is resumed rather than started afresh. `list_active_sessions` names, under `offline_in_app`, the sessions on Remote Control closed this way in the last 3 days: the Claude app still shows them, offline, and a message typed to one there waits until it is resumed.
- Why upstream should want it: without it, sessions drop out of the list with no word, and relays don't think of resuming them.
- Expect pushback: it reads a log file whose format is Claude Code's, not a documented interface. Nothing breaks if it changes: the reason is just left out.
- Test first: `tests/test_news.py` and `tests/test_courier.py`, the tests named after the rule.

### 6z''. A session compacted at a break it chooses — fork "Compact a session's conversation after the turn that asks", "Tell a session why its compaction was given up", "Log a session woken from a compaction without Remote Control", "Answer a message to a session being compacted that it is coming back"

- Status: an idea; nothing prepared, no branch.
- What: a tool, `compact_after_turn`, that a background session calls on itself, with optional instructions for the summary. Once the calling turn has ended (the session seen at rest twice in a row), the bridge stops the session, runs `claude -p "/compact <instructions>" --resume <id>` in its folder, and wakes it in place; a failed compaction still wakes it. If the turn never seems to end within 30 minutes, the bridge drops the request, logs the state it last saw and the background tasks it counted, and tells the session why. The stop archives a Remote Control session in the Claude app and the wake un-archives it, so the session is off the app's list for the length of the compaction; a session woken without its Remote Control link (claude.ai refuses to un-archive it on a device it doesn't trust) is logged. A message to the session while it is stopped is answered that it is being compacted and back in under a minute, not that it isn't running. Other sessions are refused and told to have `/compact` typed.
- Why upstream should want it: a session driven by voice has no one at the keyboard to type `/compact`, and Claude Code's own automatic compaction fires at a size threshold, mid-task. This lets the session compact at a natural break, after writing down what must survive.
- Expect pushback: it stops and restarts a session, which reloads its connectors; and it relies on `/compact` working in print mode on a resumed conversation, which Claude Code supports (its `/compact` is marked as working non-interactively) but doesn't document as a way to compact another session.
- Test first: `tests/test_compact.py`.

### 6z'''. A session that started without its claude.ai connectors, restarted — fork "Restart a session that started without its claude.ai connectors", "Wake interrupted sessions once claude.ai answers"

- Status: an idea; nothing prepared, no branch.
- What: Claude Code writes one log file per MCP server each time a session's process starts (`claude-cli-nodejs/Cache/<project>/mcp-logs-<server>/<start time>.jsonl`, naming the session on each line). At each look, the bridge checks each running session's latest start: one with logs for other servers but none for a `claude-ai-*` connector, 90 seconds on, in a folder where connectors have loaded before, came without them. A background session is then stopped and resumed in place once at rest and once claude.ai's connector proxy answers, once per start. A session busy for 10 minutes is told it will be restarted at rest; one in a window, or whose restart didn't bring them back, is told to warn the owner. A session of the Claude desktop app (`entrypoint` `claude-desktop`) is never judged: the app gives it the connectors without writing those logs. Module `connectors.py`. The bridge's own wake-up of interrupted sessions at its start waits for the proxy to answer too, up to 5 minutes.
- Why upstream should want it: after a sleep, Claude Code's daemon can respawn background sessions before the network is back; they then run without any claude.ai connector, and nothing says so until a tool is missed.
- Expect pushback: it reads Claude Code's own log files, not a documented interface (anything unexpected tells nothing, and nothing is done); the cache folder's place is the Windows one (`%LOCALAPPDATA%`), so other systems need theirs; and upstream runs sessions in tmux, where the respawn may not happen at all.
- Test first: `tests/test_connectors.py` and `tests/test_wake.py`, the tests named after the rules.

### 6z''''. An idle session parked, not stopped, so the app keeps it listed — fork "Park an idle session so the Claude app keeps it listed" (ADR-CV-107), "Leave a parked session down when the bridge starts after a restart", "Count a parked session left down after a restart as closed"

- Status: an idea; on trial in the fork since 2026-11-17; nothing prepared for upstream.
- What: `stop_active_session` parks an idle session on Remote Control with no window: it answers status `parked`, with a `say` for the user, and leaves the session at rest, for Claude Code's own close (counted from when the session last worked: an hour, 8 hours with Remote Control, a minute when memory runs low). `now=true`, or any other session, is stopped at once with `claude stop`, as upstream does. The session lists mark a parked session `parked` until it works again. After a restart, the bridge leaves a parked session down rather than waking it with the others, and counts it as closed, like one Claude Code closed: listed as shown offline in the app, and resumed by a message through the bridge. The marks live in the fork's own module `parking.py`, hooked into `server.py`.
- Why upstream should want it: `claude stop` makes Claude Code archive the session's claude.ai session, so a session stopped by voice disappears from the user's session list in the Claude app and can't be found there again; Claude Code's own close keeps it listed, shown offline, where the user still sees it and a resume brings it back (a message typed to it in the app waits until then), and Claude Code has no command for that close.
- Expect pushback: "stop" that doesn't stop; a parked session keeps its memory for up to 8 hours after it last worked; Claude Code's close and its thresholds are read from its code, not a documented contract. A `claude stop` option that keeps the session listed, asked of Anthropic, would make all of this unnecessary.
- Test first: `tests/test_start.py`, the tests named after the rules.

### 6y. Updating the bridge on Windows — fork "Update the bridge on Windows, where there is no systemd"

- What: `update_bridge` works without systemd. On Windows the bridge starts the updater as a detached process, and the updater restarts the bridge by ending it with exit code 75 (`RESTART_EXIT`), which the script that runs the bridge takes as "start me again"; a rollback does the same. The updater also checks health on the address the bridge listens on (`--host`), not always 127.0.0.1, which helps a Linux bridge bound to a Tailscale or WireGuard address too.
- Why: upstream's update relies on `systemd-run` and `systemctl`, so on Windows (and macOS) it fails at the start.
- Expect pushback: the restart needs the launcher's cooperation (a loop on exit code 75), which upstream doesn't ship for Windows; it comes with 8 (Windows in the README and CI). Tests: `tests/test_update.py`, the three Windows tests.

### 6z. Trusting a new folder on Windows — fork "Trust a new folder under the name Claude Code looks it up by"

- What: `trust_folder`, which marks a folder `start_active_session` has just made as trusted in Claude Code's config, writes its key with forward slashes (`folder.as_posix()`), as Claude Code keys a Windows folder; with the backslashed `str(folder)`, Claude Code never read it and refused the start ("Workspace not trusted"). Found in the resync's trial run.
- Why: a small bug fix on Windows; no change on Linux or macOS, where both forms are the same. Offer with 8, or alone. Test first: `tests/test_start.py`, with a Windows path, so it fails on Linux CI too.

### 6e. A manual for the assistant — fork "Give the bridge a manual, sent at connection and kept in a guide tool", cut down in "Keep in the manual only the rules that span tools"

- What: the server instructions rewritten as a short manual (roles, the rule that the owner sees nothing passing through tools, how to wait, what only the owner answers; keeping agents' words apart and the relay's scope have since moved to the owner's notes for relays, a fork-only feature), and a `guide` tool returning the same text for clients that drop server instructions. Since "Keep in the manual only the rules that span tools" it holds only what spans tools; what one tool needs is in that tool's description (6h).
- Expect discussion: the manual is written around this fork's voice relay naming; upstream would take the structure and the guide tool, and keep its own words. Test: `tests/test_guide.py`.

### 6f. "List every session" instruction and the relay label — fork "Tell the assistant to list every session it can see"

- What: one paragraph telling the assistant to name every session from every source when asked. Folded into 6e's manual; only worth offering as part of it.

### 7. Serving under a path prefix — not in the fork yet

- What: the bridge works behind a reverse proxy under a path (`CLAUDE_VOICE_PUBLIC_URL=https://host/claude-voice`) only because Nginx also forwards three root paths. One of them is the bridge's own doing: the consent form posts to an absolute `/oauth/consent` (`src/claude_voice/oauth.py`, `_consent_form`). A relative action (`action='consent'`) would remove it. The other two are where OAuth clients look for metadata (RFC 9728, RFC 8414) and can't be avoided by the bridge alone; document them.
- Why: upstream documents Tailscale Funnel only; a prefix behind an existing reverse proxy is a common setup.
- Would need: the change, a test that the form's action stays under the public URL, and a README section on running behind a reverse proxy.

### 8. Windows in the README and CI — prerequisite for the Windows ones (6y, 6z)

- Upstream says "Linux or macOS". Supporting Windows means a decision record (scope: the bridge runs on Windows; the systemd service and `install.sh` stay Linux-only; Windows runs it by hand or as a startup task), a README section, and a Windows job in `.github/workflows/checks.yml` so it stays working (today every job is `ubuntu-latest`).
- Without the CI job, Windows support would rot; upstream's own rule ("every rule is enforced by something that can fail") argues for it.
- Best offered first as an issue, asking whether Windows is wanted at all, before any pull request.
- Three of upstream's tests fail on Windows as written; two found at the 2026-11-19 resync: the safe git sweep (`tests/test_safe_git.py`) reads the source without saying UTF-8, which Windows doesn't assume (fixed here in the fork commit "Check for uncommitted changes through safe git, like every bridge git call"), and `test_a_home_folder_with_backslashes_is_read_as_it_is` (`tests/test_routing.py`) names a folder `C:\Users\gh` under the test's temporary folder, which Windows reads as a whole other path (skipped here). A third, found 2026-11-20: `tests/test_commit_hook.py` runs the hook with `sh` from PATH, which a PowerShell or cmd window lacks (fixed here in the fork commit "Run the commit-msg hook with Git for Windows' sh on Windows"). Small fixes a Windows CI job would have caught.

### 10. The owner's confirmation with a passkey — fork "Describe the owner's confirmation through the bridge", "Settle who may confirm and how exactly the words must match", "Pin the confirmation to the running commit", "Let the owner confirm a session's request from a voice chat", "Confirm with the owner's passkey on the bridge's page", "Give the asking session its confirmation link to pass on", "Give confirm_with_owner the same line for the relay as confirm_ask", "Say in the confirm tools how their line travels and what counts as an OK", "Rename confirm_with_owner to confirm_link, which names what it gives", "Show a confirmation link's end time in the link itself", "Say a confirmation covers retries, in the one place that says what it covers" (ADR-CV-105, ADR-CV-106)

- What: a session calls `confirm_ask` with the exact summary of what it wants approved and gets a short code and the request's link, which it passes to the relay (a relay given only the code gets the link with `confirm_link`); the relay sends the owner the link; the owner opens the bridge's own page, sees the request as the bridge stored it, and confirms with a passkey (fingerprint, face or PIN), whose signature the bridge checks; the session then calls `confirm_check` and acts only on "confirmed" with its own summary. `claude-voice passkey add` registers the passkey once, from a one-time link. Design, what each party can observe and the limits: `docs/owner-confirmation.md`.
- Why upstream should want it: a session rightly refuses a relayed approval, since a message from the relay looks like any other session's; so far the owner had to type "OK" at the computer. The confirmation travels by a path no model can produce, and the session checks it itself. The Claude app's approval card can't carry it: a setting skips the card, and the bridge can't see whether it was shown.
- Expect pushback: a large feature, three tools and a command; a new dependency (`webauthn`); a page served by the bridge, which answers only from a private network (the owner's VPN, the computer itself, or a tailnet), so it needs one reachable from the owner's phone. `open_folder` uses the same passkey in the fork (6), so the two may be offered together, or this one first with its own tools.
- Test first: `tests/test_confirm.py`, `tests/test_passkeys.py`.

### 11. A suite that keeps away from the owner's Claude Code — fork "Keep the owner's own Claude Code out of the suite"

- Status: an idea; nothing prepared, no branch.
- What: `build_server` looks up `jobs.background_sessions`, `jobs.background_jobs` and `transcripts.summarize` when it is built, not when `server.py` loads, and `tests/conftest.py` puts fakes there, as it already does for GitHub and the side-question reader.
- Why upstream should want it: every watched bridge in the suite ran the developer's real `claude agents` listing, and the `digest_session` case of `tests/test_api.py` made a real Haiku call on their account in every run (no login in CI, so it fails quietly there).
- Expect pushback: little; three defaults become `None`, as `side_answer` already is.
- Test first: `tests/test_wake.py` and `tests/test_digest.py` (a bridge given no helper uses the one in place when built).

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

### 15. Background steps that log their failures — fork "Log a failed background step once instead of dropping it"

- What: each step of the bridge's housekeeping loop (the look, other machines, the approval pick-up, the prompt sweep, the work board, and the fork's own steps) runs in `background_step`, which logs a failure once and again only when it changes or comes back after a recovery; the warm-up of the side-question reader logs its failure through a done-callback instead of dropping it.
- Why: upstream's loop wraps each step in `contextlib.suppress(Exception)`, so a step that breaks for good leaves no trace, against upstream's own rule that an error names what is wrong and the next step.
- Already shaped for upstream: two `# RULE` markers with their rows in `scripts/mutations/`, `tests/test_background_steps.py`. The fork's version also covers its logon wake-up; upstream's has none, so that part stays out of a pull request.
- Expect discussion: the log level (warning) and whether to log every repeat instead of once.

### 16. Reads that block the event loop, moved to worker threads — fork "Read sessions and their prompts in worker threads, off the event loop"

- Status: an idea; nothing prepared, no branch.
- What: the event API's routes for the session list, choosing the active session, one session's output, pending questions and detail, the event stream's looks, the bridge's state, the org chart and starting an update call synchronous helpers that read transcripts, run `git` and read a waiting session's prompt with `claude logs`; they now run them through `asyncio.to_thread`. The fork's own news line and waits read the prompt the same way.
- Why upstream should want it: `claude logs` takes about a second here, and a session list with two sessions waiting on a permission prompt held every other request, MCP calls included, for over two seconds. Sync MCP tools already run in a thread (the SDK does it); these routes are the async callers left.
- Expect pushback: little; the helpers must be safe to call from a thread, which they already are for the sync tools that call them.
- Test first: `tests/test_api.py`, the test named after the rule "the page reads sessions off the event loop" (a reader that notes whether a loop is running in its thread).

### 18. Printing what the bridge says to agents — fork "Print the texts the bridge serves to agents"

- Status: an idea; nothing prepared, no branch.
- What: `claude-voice texts` prints the server's instructions, every tool's name and description as `tools/list` serves them, and the guide, as JSON, from a bridge built as `serve` builds it but never started (`texts.py`). The fork's version adds its own pieces (the notes under a relayed message, the relay notes' files, what comes with the guide besides it); upstream's would print its instructions and tools.
- Why upstream should want it: upstream's `AGENTS.md` treats the client-facing text as an interface; one command that prints all of it lets a reviewer read the whole surface at once and diff it between commits, without connecting a client.
- Expect pushback: building a whole server to read its texts; the build takes a few seconds and starts nothing, and the alternative, a second list of the texts, would drift.
- Test first: `tests/test_texts.py`, which compares the printout with what an MCP client of the same bridge gets from `tools/list` and `guide`.

### 19. A beads store in fleet_recap — fork "Carry a beads store's open and recent issues in fleet_recap"

- What: a top-level `beads_export` setting names a beads store's export file, and `fleet_recap` called with `beads` true also returns that store's open issues and those closed in the last day (`beads.py`).
- Why upstream would want it: beads is a common way to track agents' work; a voice relay asked "what waits on me?" can answer from the store, and a page polling `fleet_recap` shows sessions and their work together.
- Expect pushback: it ties a general bridge to one issue tracker. Upstream may prefer it as a plugin; it reads only a file, so it would port to one easily.
- Test first: `tests/test_beads.py` was written first and seen failing.

### 20. Each session's app link wherever a session is named — fork "Give each session's app link in the session lists, starts and resumes"

- Status: an idea; nothing prepared, no branch.
- What: upstream's `app_url`, so far only in `fleet_recap`, also in `list_active_sessions` (closed sessions the app still lists included, from the job record's `cse_…` id), in the results of `start_active_session` and `attach_conversation`, which wait a few seconds for Remote Control's id, and in the guide (`app_links.py`).
- Why upstream would want it: a relay that starts or lists a session can hand the user the link that opens it on the phone, without a second call to `fleet_recap`; upstream already built the link, this only gives it where sessions are named.
- Expect pushback: the start now waits up to 5 s for an id when Remote Control is on; the wait ends as soon as the id comes (about 1.3 s when measured), and is skipped when Remote Control is off.
- Test first: `tests/test_app_link.py` was written first and seen failing.

### 21. Older conversations page by page, a search by name, and each one's kind — fork "Let list_claude_conversations page back, search by name and say each one's kind"

- What: `list_claude_conversations` takes `offset` and `query` and returns `next_offset`; each conversation says whether it ran with no window, in the desktop app or in a terminal (`history.py`); the listing runs in a worker thread.
- Why upstream would want it: today only the 50 newest can be reached, and a conversation the owner remembers by name can't be found once it is older; a voice relay asked to resume one by name needs the search.
- Expect pushback: reading every conversation on each call costs about a second on a PC with 1,600; upstream may want a cache, or the search done by the SDK.
- Test first: `tests/test_history.py` was written first and seen failing.

### 22. Sessions kept in step with the Claude app — fork "Stop a session archived in the Claude app, and copy a desktop session to the bridge"

- What: a background session archived in the Claude app is stopped (the app's archive ends its Remote Control link and Claude Code writes a code 4090 line in its conversation, but the process keeps running); an idle desktop-app session is copied to the bridge under a new id with its history (`--resume --fork-session`), since the desktop app won't take typing to its own session once the bridge runs it (`app_sync.py`).
- Why upstream would want it: an archived session goes on running unseen, holding memory and a slot; a message typed to it in the app waits unseen.
- Expect pushback: reading Claude Code's words in the conversation is not a documented interface; copying desktop sessions suits an owner who never uses them, so upstream may want it behind a setting, off by default.
- Test first: `tests/test_app_sync.py` was written first and seen failing.

### 23. No statement cache on the shared database connection — fork "Keep threads from reading each other's rows from the bridge's database"

- What: the store's one connection, shared by threads (`check_same_thread=False`), is opened with `cached_statements=0` (`store.py`; upstream's `store.py:135`). sqlite3's statement cache hands threads running the same SQL one prepared statement, so they get each other's rows, none, or an error; the lock on writes doesn't cover reads.
- Why upstream would want it: sync tools run in worker threads and the watcher reads from its own, so any two overlapping reads can cross. Here it crashed `list_claude_conversations` 12 times on 2026-11-22, and `start_active_session` and `fleet_recap` before (in `access.granted`); it can also silently swap or drop a setting, a session number or a rename (fork review COR-1, 2026-11-19).
- Expect pushback: each statement is prepared again on every call; the bridge's queries are few and small, so the cost doesn't show.
- Test first: `test_a_read_from_one_thread_never_gets_another_threads_row` (three threads reading their own setting for 1.5 s) was written first and seen failing.

### 24. Held folders swept on Windows too — fork "Sweep the helpers' folders on Windows too"

- What: on Windows the held folder's lock is a file its holder keeps open (`os.open` without delete sharing), and the sweep removes a folder whose lock it can delete, or one with no lock past the grace time; links and junctions are never followed (`held_folder.py`).
- Why upstream would want it: upstream runs on Windows too, where a killed bridge's helpers and the ratchet's runs leave their folders for good; here 606 piled up in under a month.
- Expect pushback: deleting the lock is how the sweep asks whether it is held, which is a probe that acts; it only acts when nobody holds the lock. No owner is compared on Windows, where the temporary folder is the user's own.
- Test first: `tests/test_held_folder_everywhere.py` was written first and seen failing on Windows.

### 25. X-Forwarded-For read only from this computer — fork "Read X-Forwarded-For only from a proxy on this computer"

- What: `caller_address` (`api.py`) reads the last `X-Forwarded-For` entry only when the request comes from this computer, as Tailscale's do; a request from another machine is that machine's address.
- Why upstream would want it: anything that reaches the bridge's port directly (another machine on the LAN, a VPN peer, a container) can send `X-Forwarded-For: 127.0.0.1` and pass the gate on `/api/` and `/ui`, which then stands on the bearer token and sign-in alone. Tailscale's serve and Funnel connect from this computer, so they work as before.
- Expect pushback: a proxy on another machine (nginx on a server, say) no longer passes on its caller's address, so the bridge sees the proxy. The event API stays closed through it, as before, since its callers were never this computer or the tailnet.
- Test first: `test_another_machine_cannot_claim_to_be_here_or_on_the_tailnet` was written first and seen failing.

## Not for upstream

- The owner's setup around the fork (Windows service scripts, the server proxy, its values and working notes), kept in a private repository: specific to that setup.
- The fork's `CLAUDE.md`, section "The owner's fork", after upstream's own pointers (fork "Say the fork is AI-managed"): says the fork is AI-managed, maintained by kelno's own agents under kelno's own rules, and that for those agents the gates run as slow checks rather than before every push, with no ratchet on the fork's own `main` and the suite on 8 parallel workers (14).
- The guide's line saying the bridge is the owner's own software, so a change to it is a fair suggestion when a tool gets in the way (fork "Tell the relay the bridge is the owner's own project", then "Call the change feedback names a lead, not the fix"): true only of this fork.
- The commit-msg hook calls `python3`, which the python.org installer doesn't create on Windows (only `python.exe`), so the hook fails there unless something else provides `python3`. Worth a one-line issue at most, not a fork change.
- Handling the owner's own sessions like the bridge's (fork "Let the bridge handle the owner's own sessions like its own", ADR-CV-108): upstream stops only what the bridge started, on purpose; the fork widens that, and its own features limited the same way, because its owner has one session coordinate all the others. A choice about the owner's own workflow, not a fix. Where a candidate above is offered upstream, it goes in upstream's shape, for the sessions the bridge started.

## Merged upstream

Our pull requests upstream merged, newest first.

- [#129](https://github.com/roykollensvendsen/claude-voice/pull/129), every mutation row checked to compile (2026-11-10, `f12686d`): a row whose change leaves the code unable to load is reported as broken, and the suite applies every row and requires the result to compile, so a broken row shows on any run, not only in the mutation job.
- [#109](https://github.com/roykollensvendsen/claude-voice/pull/109), `wait_for_session` (2026-11-10, `08fe40c`): a caller waits for a session's turn to end, or for it to need its user, without sending anything. Three rounds of review; the fork's version goes further without `after` (`UPSTREAM.md`, "Same tools, different behaviour").
- [#108](https://github.com/roykollensvendsen/claude-voice/pull/108), each tool says what it is for in its first line (2026-11-10, `a3fa2ce`): every description opens with one whole sentence of at most 78 characters, since the Claude app shows only that much until a tool is loaded. Upstream keeps its own words for the lines; the author plans a follow-up for the connector-name prefix the app later put inside that cut.
