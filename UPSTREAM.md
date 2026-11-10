# Upstreaming the Windows fork

What in this fork, [kelno/claude-voice](https://github.com/kelno/claude-voice), could go back to [claude-voice](https://github.com/roykollensvendsen/claude-voice) by Roy Kollen Svendsen as pull requests, how the fork differs from it and why, and the decisions taken when bringing the two back together. Written to be picked up later, cold, by people and agents.

- "The owner" is kelno, who maintains this fork; "the relay" is the voice assistant in the Claude app that talks to the bridge for them; "agents" are the Claude sessions that do the work.
- The fork split from upstream at `21d1d96` (2026-10-29) and was rebuilt on upstream's `9caf3f4` in the resync of 2026-11-06, every fork commit replayed on top; the history from before that rebuild, with the commits upstream already had, is on the fork's branch `main-before-resync`. Since the resync of 2026-11-08, a resync merges upstream's `main` into the fork's instead (upstream's `52c296d` that day), so the fork's commits keep their hashes from then on. Fork commits are still named here by their subject (find one with `git log --grep`), since the rebuild of 2026-11-06 renamed them once. Upstream's commits keep their hashes. Upstream moves fast; check what it has taken before offering anything.
- Upstream has taken some of this already (the Windows liveness, memory and UTF-8 fixes, the access-token lifetime and the log file), and its author invited pull requests in [discussion #97](https://github.com/roykollensvendsen/claude-voice/discussions/97).

## Upstream's rules, which every pull request must follow

From upstream's `CONTRIBUTING.md`; read it again before starting, it may have changed.

- One pull request per change, landed by rebase, so each commit must stand alone: green, linted, readable without the others. No fixup or work-in-progress commits.
- A test is written first and seen failing. A line marked `# RULE:` needs a row in `scripts/mutations.toml` and a test named after the rule.
- A choice that costs more to reverse than to take gets a decision record in `decisions/` first (`ADR-CV-NNN-…`, sections Status, Context, Options considered, Decision, Consequences, Related).
- Gates: `ruff check`, `ruff format --check`, `mypy src`, `pytest -q --cov`, `committed` on the commit messages, and `scripts/clean_install.py` when the README's first steps or packaging change.
- Commit messages: Conventional Commits, imperative subject under 72 characters, a body written for someone who has never seen the project, mechanics at the bottom under a heading. `scripts/imperative.py` checks the imperative.
- Facts in prose that also live in code need a test in `tests/test_documentation.py`; every `console` block in a page is run by `tests/test_documented_commands.py`.
- Upstream's own habit: a `test: Describe …` commit whose body ends "This test fails until the next commit.", then the `feat:` or `fix:` commit. No `Claude-Session:` line in their messages (`scripts/no_session_links.py` refuses it).
- `scripts/ratchet.py check` fails a pull request whose `quality.json` doesn't record the levels it reached, and a change that adds a copy of a line a `scripts/mutations.toml` row looks for can make that rule's mutation miss (seen on `wait_for_session`).

## How we check a pull request here (the owner's call, 2026-11-06)

The full ratchet switches every rule off in turn (about ten minutes, 44 test runs) and gets stopped for low memory on this PC, so the mutation sweep is left to upstream's CI, which runs the full ratchet on every pull request. Work in a separate worktree of the fork on upstream's `main`, never in the checkout the bridge runs from. Locally, for each pull request:

1. The tests the change touches, `ruff check`, `ruff format --check` and `mypy src`; each new test seen failing at the `test:` commit. When the change adds or edits a row in `scripts/mutations.toml`, apply that one row by hand and run its rule's test: the runner replaces the whole matched line with `replace`, keeping only the indentation, so the replacement must be a complete line, the module must still import, and the rule's own test must fail. Then put the line back. The local run skips mutations, so without this check a broken row shows up only in CI, as a rule killed by another test: on #109 (2026-11-10), a row cut off mid-expression broke the import and left the rule unproven.
2. `uv run python scripts/ratchet.py update --skip-mutation`, with `quality.json` committed in the `feat:` or `fix:` commit (it runs the whole suite once, about 90 s). Then put back main's `test_seconds`, and main's `coverage` too when the change can't move it (wording only): this PC is slower than the author's and measures coverage a little lower, so its figures would become everyone's baseline. The author asked for this on both #108 and #109, twice (2026-11-06 and 2026-11-07).
3. Push the branch to the fork (`origin`, push URL `github-agents:`), never to `upstream`. The session opens the pull request itself, as a draft (`gh pr create --draft --repo roykollensvendsen/claude-voice`), and the owner reviews it, edits it and marks it ready. Its text opens with a disclaimer saying an AI wrote it on kelno's behalf, and doesn't speak as the owner. If CI then reports a mutation that survived, fix it and push again.
4. Replies to the author's reviews are drafted in the owner's private notes and posted by the owner, from their own account, after editing them (owner, 2026-11-08); the agents' account doesn't post them.
5. From the resync on, this is done in upstream's contribute session (`start_role`), set up as in resync decision 7 (owner, 2026-11-06). Opening the draft needs the GitHub command-line tool (`gh`) installed and signed in with the agents' GitHub account; until then, the session pushes the branch and the owner opens the pull request.

## Resync decisions (2026-11-06)

The fork was rebuilt on top of upstream's `main` (`9caf3f4`; 132 commits behind, 69 of its own at the time), replaying fork commits one at a time, oldest first, and dropping those upstream already had. The owner's choices, one per point where the two diverged, and how each came out:

1. Wait limits: take upstream's per-client time budgets (one limit per tool call: 20 s for a call from another computer, counted as the Claude app; 2 min for upstream's voice app; 60 s otherwise), and keep the fork's 8 s default wait on top. Condition: a test that the voice relay's calls, which come through the owner's server proxy, get the Claude app's 20 s; if the bridge sees them as local, pin them to 20 s. Done: a call through the proxy comes from another computer's address, so it counts as the Claude app's and gets 20 s, with or without a forwarding header; `tests/test_cap.py` keeps it, no pin needed.
The owner's general rule: align with upstream, refactoring the fork to match it, as long as every fork feature is kept.

2. Tool titles: settled by the rule. Upstream's shared titles table, with the fork's own tools added to it.
3. Overlapping tools: settled. `digest_session` becomes upstream's, with anything the fork's does on top. Upstream's `side_question` is the fork's `peek_session` under its old name, unchanged (same code; upstream's only change is where its title is stored), so the fork keeps `peek_session` and doesn't take `side_question`. The side session (`ask_forked_session`, `message_forked_session`, `read_forked_session`) stays, for digging deeper (owner, 2026-11-06).
4. Starting sessions: settled (owner, 2026-11-06). The fork's way stays the default: a session started by voice runs in the background on Remote Control. Upstream's terminal window is kept as an optional setting for one start. Why: upstream's window solves the problem the fork already solved (sessions out of reach from the phone); the owner finds the fork's way cleaner, and upstream's window works only on Linux unless a terminal is named in its settings. Offered upstream as candidate 6x.
5. Folder trust: settled by the rule. Upstream's trust of folders it made, plus the fork's trust of folders opened by path.
6. Message tools marked destructive: settled by the rule, the fork's choice is a feature. Messages stay unmarked, so the Claude app doesn't ask for a tap on each one.
7. Upstream-only features (owner, 2026-11-06): all the code comes in; what's switched on:
   - Self-update (`update_changes`, `update_bridge`): on. It pulls the branch the bridge's clone follows, which for the fork is the fork's `main` on GitHub, never upstream, and only as a fast-forward; it refuses to start with local changes, runs the tests, and rolls back on failure, so it can't overwrite fork changes. It doesn't bring upstream's changes: those still come by a resync. What an update brings is read from the commit messages, grouped by kind, not from release notes. On Windows the updater runs as a detached process and restarts the bridge by ending it with exit code 75, which the owner's start script takes as "start again" ("Update the bridge on Windows, where there is no systemd", candidate 6y). Its full test run takes about 100 s here.
   - Contribute role (`start_role`): on. Needs the role set up for this PC in `~/.config/claude-voice/roles.toml`: its folder on a separate worktree, not the clone the bridge runs from; its first message following "How we check a pull request here" above (no full mutation sweep, push through `github-agents:`, open the pull request as a draft with `gh` for the owner to review and mark ready), since upstream's asks it to run the whole mutation script and open ready pull requests. Needs `gh` installed and signed in with the agents' account. Set up at the resync, on a separate worktree of the fork on upstream's `main`; `gh` isn't installed yet, so for now the session pushes the branch and the owner opens the pull request.
   - Chosen session (`get_active_session`, `set_active_session`): off. Their code stays, close to upstream's, but the fork doesn't register them (`SWITCHED_OFF` in `server.py`; "Keep the switched-off upstream tools' code, offered to no one").
   - Companion app: off. Its API code is in, without its parts for sessions run inside the bridge; its event stream reads the watcher's events through `event_feed()`, a plain function, since `whats_new` is no longer a tool ("Remove whats_new now that every result carries the news").
   - `refresh_session_summary`, whose summary also shows on the companion page: off (owner, 2026-11-06), since the resync's cold read showed it overlapping `digest_session`. Its code stays, but it isn't registered, the same way ("Keep the switched-off upstream tools' code, offered to no one").
   - Plugins: code in, off; no other MCP servers on this PC for now.

## Resync decisions (2026-11-08)

Upstream's `main` (`52c296d`) was merged into the fork's, 39 upstream commits since the last resync, with 117 fork commits of its own since then. The owner's choices, and how each came out:

1. Method: a merge, not a rebuild (owner, 2026-11-08). Each conflicting area is resolved once, in the merge commit, against the fork's current code, instead of once per replayed commit; the fork's history is never rewritten, so its `main` is never force-pushed, and upstream's commits keep their hashes. A pull request to upstream is built on its own branch from upstream's `main` anyway, so the fork's history doesn't need to be in that shape. The merge commit only resolves the merge; what it called for beyond that came after it, one commit each: the rename merge (2), the renumbering (3), these notes, and `quality.json`.
2. Other machines, "the hub" (`agent.py`, `fleet.py`, `machines.py`, `tailnet.py`, pairing on the command line, ADR-CV-006 to 008, `docs/other-machines.md`): taken in as upstream has it, inactive. Nothing is paired on this PC and Tailscale isn't needed: with no machine, the fleet's middleware passes every call through and its ten-second refresh asks nobody. Two fits to the fork's code: the agent's own bridge is built without the approval broker the fork retired, and the agent's list of session tools names some the fork retired, so it offers only those the fork's bridge has.
3. Renaming sessions: upstream's `rename_session`, with its arguments (`session`, `new_name`), its rule for names (letters, digits, `.`, `_`, `-`) and its old names that still reach a session, for any running session; plus, for a session the bridge started, the fork's write into Claude Code's job record in place of upstream's typing into tmux, which Windows doesn't have. The fork's rule "only a session the bridge started is ever renamed" became "only a session the bridge started is renamed in Claude Code itself". What a rename returns (`claude_code`): `renamed` for a session the bridge started, renamed in Claude Code too and shown in the lists, the phone and the desktop app within seconds; `busy` when that session is mid-turn (renamed in the bridge; rename again once idle); `bridge_only` for any other session, with a note that the new name is the bridge's only and Claude Code and the desktop app keep the old one (also when the job record can't be written); `already` when Claude Code has the name. Nothing is typed into tmux; `renames.py` stays as upstream has it, its tmux part unused. ("Rename a session the bridge started in Claude Code itself".)
4. Decision records: upstream took ADR-CV-006, 007 and 008, which the fork had used too. Upstream keeps its numbers; the fork's six move to 009 to 014 in the order they were written, every reference with them (see "Decision records renumbered" below; "Renumber the fork's decision records after upstream's").
5. `wait_for_session`: the fork's, kept as it was before the merge.
6. Upstream's tests for the hub and for renaming compare a delivered message without the note the fork adds to every relayed message.

## Differences from upstream

Every way the fork differs from upstream's `main`, by what an agent calling the tools or the owner sees, with why and the fork commits. Rebuilt after the resync of 2026-11-06 and checked again at the merge of 2026-11-08 (upstream `52c296d`); keep it current with each fork commit and each resync. Nothing here is a guess unless marked so. The candidate entries below have the detail for what could go upstream.

### Decision records renumbered

Upstream's ADR-CV-006 to 008 (other machines, a phone as an agent, approvals on the phone) came after the fork had used those numbers; at the resync of 2026-11-08 the fork's records moved up by three ("Renumber the fork's decision records after upstream's").

| Old fork number | Fork number now | Record |
| --- | --- | --- |
| ADR-CV-006 | ADR-CV-009 | Folders outside the root are opened one at a time, by the owner |
| ADR-CV-007 | ADR-CV-010 | Desktop scratch folders can be opened, and access is named for what is missing |
| ADR-CV-008 | ADR-CV-011 | The side session is a read-only fork |
| ADR-CV-009 | ADR-CV-012 | The side session works on past the caller's time budget |
| ADR-CV-010 | ADR-CV-013 | The owner confirms on the app's card, in a voice chat |
| ADR-CV-011 | ADR-CV-014 | The owner confirms with a passkey on the bridge's page |

### Renamed

| Upstream name | Fork name | Why | Fork commit |
| --- | --- | --- | --- |
| `side_question` | `peek_session` | Says it's a quick look at what the session wrote, next to the side session that can read code; same code otherwise | "Rename the quick question to peek_session and say what each reader sees" |
| `wait_for_session` (waited for a session the bridge ran itself) | `wait_for_session` (waits for any running session's turn to end) | Same name, a different tool: the old one went with the in-bridge sessions, the new one is offered upstream as a pull request | "Retire the tools for sessions run inside the bridge", "Let a caller wait for a session's turn to end without sending" |

### Tools only in the fork

| Tool | What it's for | Why | Fork commits |
| --- | --- | --- | --- |
| `ask_forked_session`, `message_forked_session`, `read_forked_session` | The side session: a read-only copy of a busy session's conversation that answers a question and can read the code | Dig deeper into a session that's working, without disturbing it | "Ask a read-only fork of a busy session while it keeps working", "Read past a resumed session's leftover turn, and never call silence an answer", "Let the side session work on past the caller's time budget" |
| `guide` | The manual for the calling agent; also handed to a conversation that never read it | The Claude app doesn't pass the server's instructions to the model | "Give the bridge a manual, sent at connection and kept in a guide tool", "Keep in the manual only the rules that span tools", "Hand the manual to a conversation that never read it", "Move the bridge's delegation rules from memory into the guide" |
| `hand_to_desktop` | Hands a session the bridge started to the desktop app | Carry on at the PC | "Hand a session the bridge started to the desktop app" |
| `watch_session` | Adds a session to the news line on every result, or takes it out; the sessions the relay starts, resumes, messages or reads are added by themselves | The relay's own set of sessions to follow, one set across its voice calls, kept over a bridge restart. Upstream's chosen session (`get_active_session`, `set_active_session`, switched off here) is one session, the one the companion page talks to; the owner wanted the idea in this form instead: several sessions, for the relay's news | "Watch sessions for the news line, and name only what needs the relay" |
| `confirm_ask`, `confirm_with_owner`, `confirm_check`, and the `claude-voice passkey` command | A session asks for the owner's own OK; the owner confirms it with a passkey on the bridge's own page (ADR-CV-014); the session checks the result itself. Design and reasoning: `docs/owner-confirmation.md` | Sessions rightly refuse an approval relayed by message, so the owner had to type OK at the PC; this lets the owner confirm from the phone in a way the session can verify | "Describe the owner's confirmation through the bridge", "Settle who may confirm and how exactly the words must match", "Pin the confirmation to the running commit", "Let the owner confirm a session's request from a voice chat", "Confirm with the owner's passkey on the bridge's page" |
| `request_access`, `revoke_access` | Open a folder outside the root, by session or by path, once the owner confirms it with a passkey, and close it again; a folder opened by path is also trusted by Claude Code | Work anywhere on the PC, one folder at a time, with the owner's approval; the app's approval card can be skipped by a setting, so the passkey carries it | "Let the owner open folders outside the root, one at a time", "Name access states plainly and open desktop scratch folders", "Open a folder by its path with request_access", "Trust a folder in Claude Code when the owner opens it by path", "Confirm with the owner's passkey on the bridge's page" |

### Upstream tools the fork removed or switched off

A switched-off tool keeps its code, as close to upstream's as possible, and only isn't registered: it is named in `SWITCHED_OFF` in `server.py`, and removing it from there turns it back on.

| Tools | Why | Fork commits |
| --- | --- | --- |
| `send_task`, `session_recap`, `cancel`, `close_session`, `list_sessions`, `create_session`, `approve`, `deny`, `list_pending_approvals` | They served sessions run inside the bridge, which the owner couldn't open on the phone or at the PC and whose prompts came only through the relay; all work now runs as ordinary sessions on Remote Control | "Start new work as background sessions, a window only when asked", "Resume earlier conversations as background sessions", "Retire the tools for sessions run inside the bridge" |
| `get_active_session`, `set_active_session` | The chosen session marks which session the owner talks to, for the companion page; the owner switched it off (resync choice 7), and tools the relay sees but never uses are noise in its list. The companion API's `/api/v1/active` routes stay. Switched off, code kept | "Drop the chosen-session tools the owner switched off", "Keep the switched-off upstream tools' code, offered to no one" |
| `refresh_session_summary` | A second tool summing up a session, next to `digest_session`; a fresh agent reading the tools couldn't tell the two apart. The summary stays on the companion page, made by its own route. Switched off, code kept | "Drop the second summary tool, leaving digest_session", "Keep the switched-off upstream tools' code, offered to no one" |
| `whats_new` | No relay thought to call it; every result now carries a line on what the relay hasn't read. The companion API's event stream still reads the watcher's events, through `event_feed()`, a plain function | "Tell the relay in every result what it hasn't read yet", "Remove whats_new now that every result carries the news" |

### Upstream tools new to the fork with the resync

Added upstream after the fork split off, and in since the resync (choice 7): `check_dependencies`, `start_role` (the contribute role, set up for this PC), `update_bridge` and `update_changes` (self-update, with a Windows restart: "Update the bridge on Windows, where there is no systemd").

### Same tools, different behaviour

| What | Upstream | Fork | Why | Fork commits |
| --- | --- | --- | --- | --- |
| How long a call waits | A limit per client (20 s for the Claude app, 2 min for its voice app, 60 s otherwise) | The same limits, with a 5 s default wait under them | The Claude app drops a call at about 30 s, and its voice mode speaks up after about 10 s; through the owner's server proxy an 8 s wait crossed that once (2026-11-06), hence 5 s (resync choice 1: take upstream's limits, keep a short default) | "Let the side session work on past the caller's time budget", "Wait 8 seconds by default, before voice mode backgrounds the call", "Wait 5 seconds by default", "Wait for a session's reply by default, and in silence" |
| Approval taps on messages | `message_active_session`, `ask_active_session` and `stop_active_session` marked destructive, so the Claude app asks for a tap on each | Not marked destructive | A tap on every message makes the voice loop unusable; the receiving session keeps its own permissions | "Stop asking the owner before every message to a session", "Stop a session the bridge started without asking the owner first" |
| Starting a session | In a terminal window or in the background, as the settings say, the window by default | In the background on Remote Control by default; upstream's window for one start, with `start_in`; on Opus unless a model is asked for | Sessions the owner can open on the phone and at the PC, with no window needed; Opus for everyday work (resync choice 4) | "Start new work as background sessions, a window only when asked", "Say what to do when Claude Code doesn't know a session's model", "Start a session on Opus when no model is asked for" |
| Continuing an earlier conversation (`attach_conversation`) | A copy run inside the bridge | Resumed as a background session on Remote Control | Same as above | "Resume earlier conversations as background sessions", "Continue a former background session instead of copying it", "Run resumed sessions with their own permissions" |
| Reading a session (`read_session_output`) | Six turns by default, a `latest` field when far behind | Upstream's `latest` field, but a text budget instead of six turns; the first turn that doesn't fit is cut and shown rather than hidden | A long reply after a short turn came back as "nothing new" | "Fill a text budget when a read asks for no count", "Tell the assistant to read on until no turns are left", "Start the first turn a read has no room for instead of hiding it" |
| Every result | Nothing added | A news line on the sessions the relay watches, each with its state and only what needs the relay (a reply to its message, something the owner typed there, a permission prompt, the session having ended) or "nothing new", plus any session on the PC waiting on a permission prompt; and notes on late results and on waits still running | The relay missed news, and copied the app's late-result calls; a first version counted every unread message, and one busy session's progress lines drowned the rest until the relay stopped reading the line; and a send counted as reading, so a report that ended between two sends never showed | "Tell the relay in every result what it hasn't read yet", "Watch sessions for the news line, and name only what needs the relay", "Keep a reply as news until the relay reads it, even after another send", "Remind the assistant where a late result comes from", "Tell the assistant a wait on the session is still running" |
| Session lists | Terminal or background | Desktop-app sessions shown as "desktop", side chats marked | The owner uses the desktop app | "Call a session open in the desktop app a desktop session", "Mark the desktop app's side chats in the session lists" |
| Project list (`list_projects`) | Folders under the root | Every folder Claude Code was used in, wherever it is | The owner's projects aren't under one root | "List the folders Claude Code was used in, wherever they are" |
| Messages written into a session | As sent | Say that the relay reads the reply | So the session answers an agent, self-contained | "Tell a session who reads its reply when the relay writes to it" |
| Bridge start | Nothing | Wakes the sessions a shutdown interrupted | The PC sleeps and restarts | "Wake the sessions a shutdown interrupted when the bridge starts", "Wake what was running at the bridge's last look, not by boot time" |
| Renaming a session (`rename_session`) | Any running session renamed in the bridge, old names still reaching it; Claude Code itself renamed by typing `/rename` into the session's tmux pane when it is idle; names of letters, digits, `.`, `_` and `-` only | The same in the bridge, with any text on one line as a name, as a start takes; a session the bridge started is renamed in Claude Code through its job record once its turn has ended, any other keeps its name in Claude Code (`claude_code: bridge_only`), and nothing is typed into tmux | Windows has no tmux, and the job record reaches the phone and the desktop app and lasts past a wake (resync 2026-11-08, choice 3); upstream's narrow names were for typing into tmux, and a session could be started under a name it couldn't be renamed back to (owner, 2026-11-08) | "Rename a session the bridge started", "Write a new name in the compact form Claude Code uses", "Rename a session through its job record, where Claude Code looks", "Rename a session the bridge started in Claude Code itself", "Let a rename take any name a start takes" |
| The agent's session tools (other machines) | The agent offers its list of session tools | The same list, less the tools the fork retired | The fork's bridge doesn't have them (resync 2026-11-08, choice 2) | "Merge upstream's other machines and session renaming" |
| Waiting without `after` (`wait_for_session`) | In pull request #109: only what the session writes during the wait | All the relay hasn't read of that session, from where it last read it (the news line's mark); from the call when it never read it | A wait must never skip anything the relay hasn't seen (owner, 2026-11-08); upstream has no record of what a caller read, so its version can't do this | "Give a wait without after all the relay hasn't read" |
| First line of each description | Runs past the Claude app's cut | One sentence within 78 characters | The app shows only that line until a tool loads; offered upstream as a pull request | "Say what each tool is for in the first line of its description" |
| Wording | "background" sessions | "Remote Control" sessions | Agents told the owner sessions were hidden when they were on the phone | "Present the bridge's sessions as on Remote Control, not background" |
| Updating the bridge (`update_bridge`) | Through `systemd-run` and `systemctl`, so Linux only | Also on Windows: a detached updater, and a restart by exit code 75 that the start script answers | The bridge runs on Windows here (resync choice 7) | "Update the bridge on Windows, where there is no systemd" |
| Tests before an update restarts (`update_bridge`) | Always run | Run only when asked (`run_tests`, `--tests`); the rollback when the new version doesn't come back healthy stays | Only the owner's own sessions commit to the fork, and they test before pushing, so the run only delayed the restart (owner, 2026-11-06). Ask for it after a resync or changes made elsewhere | "Run an update's tests only when asked" |

## Most promising for upstream (ideas, 2026-11-06)

Beyond the two pull requests already offered ([#109](https://github.com/roykollensvendsen/claude-voice/pull/109) `wait_for_session`, [#108](https://github.com/roykollensvendsen/claude-voice/pull/108) the short first lines), the fork features most likely to help upstream, most promising first. Ideas only: nothing prepared. Each points to its candidate entry below.

0. The relay waits on a session and carries on by itself (owner, 2026-11-06: "a big improvement over upstream"). The pieces: `wait_for_session` with `after` (in the `pr/wait-for-session` branch); `message_active_session` returning `next_after`, so a send hands the wait its starting point (6d', not in the branch; upstream's send returns none, so the relay has to read first); the 8-second default wait, under voice mode's ~10-second move to the background (6l, not in the branch, which waits up to the client's 20-second budget); the manual's "Waiting" rules, wait again on "still_working", in silence, answer the owner if they speak (6l, 6e, not in the branch); the `pending` note on a session already being waited on (6m, not in the branch). The move to the background itself is the Claude app's, not the bridge's. Guess: the news line on every result ("Tell the relay in every result what it hasn't read yet") helps the relay notice replies but isn't what makes it wait. Offer as a follow-up to `wait_for_session`: first `next_after` on a send, then the short default and the waiting rules.
1. Messages to a session not marked destructive (6k): the Claude app asks for a tap before every message and offers only "Allow once", which every voice user of upstream hits on every message.
2. The manual handed to a conversation that never read it (6n, with the `guide` tool, 6e): the Claude app doesn't pass server instructions to the model, so upstream's instructions likely never reach its Claude-app users either.
3. A short default wait (6l): voice mode backgrounds a call after about 10 seconds and makes the assistant announce it; an 8-second default fits under upstream's per-client budgets and keeps voice calls quiet.
4. Starting sessions in the background on Remote Control (6x): solves the problem upstream's new terminal window solves, with no desktop, terminal or tmux needed, on every platform.
5. Waking the sessions a shutdown interrupted (6s): upstream's bridge also runs on computers that sleep or restart, and today the interrupted sessions just stay stopped.
6. A read that starts the turn it has no room for (6i): upstream's `latest` field covers the far-behind case, but a long reply after a short turn can still come back as "nothing new".
7. The read-only side session (6d): upstream has only the quick look from what a session wrote; this one can read the code, at a cost upstream would want to weigh.
8. Folders opened by path, with Claude Code's trust (6o, 6o'): upstream now trusts folders it makes; opening an existing folder anywhere on the computer is the next step, though it writes Claude Code's config file, which upstream may resist.

## Candidates

In the order they'd best be offered. Each fork commit already follows the rules above (test first, gates green, message style), so each is a starting point, not a finished pull request.

### 1. Liveness without signals — fork "Check that a session is running without ending it on Windows"

- Taken upstream before the resync; the fork uses upstream's version now (the fork's commit is on the branch `main-before-resync`).
- What: whether a session's process is alive is asked through `psutil.pid_exists` (new `procs.py`), not `os.kill(pid, 0)`.
- Why upstream should want it: on Windows `os.kill(pid, 0)` ends the process, so the bridge closes every Claude Code session it checks, every 5 seconds. Harmless on Linux and macOS either way, so it changes nothing for current users.
- Cost: a new dependency, psutil (and types-psutil for mypy). Upstream may prefer a tiny platform switch (keep `os.kill` on POSIX, `OpenProcess` through ctypes on Windows) to avoid a dependency; offer both.
- Test: `tests/test_liveness.py` fails if the check sends any signal. It is red on Linux too before the fix, so upstream's Linux-only CI can see it.
- Likely ask: a decision record on supporting Windows at all (see "Prerequisite" below), since this is only worth it if Windows is in scope.

### 2. Memory figure without /proc — fork "Report the bridge's memory use on every platform"

- Taken upstream before the resync; the fork uses upstream's version now (the fork's commit is on the branch `main-before-resync`).
- What: `health`'s memory figure from psutil instead of `/proc/self/status`.
- Why: it reads zero on macOS today too, not only Windows. Small, depends on 1 for psutil.
- Test: the existing health test; on Linux it passes before and after, so the red step is only visible on macOS or Windows. Could be folded into 1.

### 3. UTF-8 for every text file — fork "Read and write text files as UTF-8 on every platform"

- Taken upstream before the resync; the fork uses upstream's version now (the fork's commit is on the branch `main-before-resync`).
- What: every `read_text`, `write_text` and text-mode `open` in src, the hook, scripts and tests names `encoding="utf-8"`.
- Why: Claude Code writes its transcripts in UTF-8; without an explicit encoding Python uses the system's, which is cp1252 on Windows, and reading a session's output crashed on the first curly quote. Upstream's own test tooling hits it too. Also protects Linux machines with a non-UTF-8 locale.
- Test: `test_a_transcript_is_read_as_utf8_whatever_the_system_encoding`. Problem for upstream: on Linux CI it passes before the fix (UTF-8 locale), so the red step isn't visible there. Options: run the test under a forced legacy encoding (for example by monkeypatching `locale.getpreferredencoding` and the `io.text_encoding` path), or add a ruff rule that refuses `read_text()` without an encoding (`PLW1514`, unspecified-encoding) as the mechanism. The ruff rule fits upstream's "every rule is enforced by something" best.
- Independent of 1 and 2; the easiest to accept.

### 4. Waiting for a session — fork "Wait for a session to finish or need an approval"

- Offered 2026-11-06 as [#109](https://github.com/roykollensvendsen/claude-voice/pull/109), rebuilt as fork "Let a caller wait for a session's turn to end without sending" on upstream's time budgets (branch `pr/wait-for-session-2`).
- Reviewed by the author 2026-11-06, four requests, all done, pushed, and the reply posted: turns capped at 30 with `turns_left` and `latest`; one helper for the final-reply rule (`reply_is_final`, also taken into the fork as "Keep a session's words when it ends during a wait"); a session that ends during the wait keeps its words (also in the fork); main's `test_seconds`. Waiting for the author.
- Second review 2026-11-07: rebase on main, and two questions. Done 2026-11-08, pushed and answered: without `after` only what the session writes during the wait comes back (the author's suggestion, as `ask_active_session` does); with `after`, a turn that took the message and wrote nothing ends the wait as idle with an empty reply. The fork goes further without `after` (see "Same tools, different behaviour"), mentioned to the author as a possible later pull request. Waiting for the author.

- What: tool `wait_for_session(session_id, wait_seconds=60)`, up to 300 s, returning the recap with `waited` = finished, needs_approval, still_running, not_running. The instructions to the assistant use it after `send_task`.
- Why: a voice client can't be notified, so it polled `session_recap` and the person had to remind it to look; in a real voice test the assistant asked for exactly this.
- Expect pushback: upstream keeps the tool surface small (`tests/test_surface.py`). The alternative it may prefer: a `wait_seconds` parameter on `session_recap` instead of a new tool. Offer that shape first.
- Unknown worth settling first: how long claude.ai waits for one tool call before giving up. Measure before proposing a default.
- Follow-up fix, fork "Say a task finished when it ended before the wait began": a task that ended before the wait began reported "not_running"; now "finished" when the session was ever given a task. Fold into the same pull request.
- Test: `tests/test_wait.py`.

### 5. Side chats marked — fork "Mark the desktop app's side chats in the session lists"

- What: running sessions whose name starts with "btw:" carry `side_chat: true` in `list_active_sessions` and `fleet_recap`; the instructions say not to send work there unless asked.
- Why: the Claude desktop app (macOS too, so not Windows-specific) runs a side chat as its own session, and the assistant can't tell it from a project session.
- Weakness: it relies on the app's naming, not a field (the session registry has none). Say so in the pull request; upstream may prefer to wait for a real field from Claude Code.
- Test: `test_a_desktop_side_chat_is_marked_as_one`.

### 6. Folders outside the root, opened by the owner — fork "Let the owner open folders outside the root, one at a time"

- Follow-up, fork "Name access states plainly and open desktop scratch folders" (ADR-CV-010): values renamed `folder_not_open` / `folder_closed` (the first collided with tool-call approvals), refusals name the closed folder and the relation (inside / holds), and an exact-depth exception opens the Claude desktop app's scratch folders under `AppData` (`# RULE` "only a Desktop scratch folder is opened inside a denied folder"). The exception is Windows-desktop specific; upstream may want it behind a setting.
- What: every running session is listed; one outside the root and the granted folders shows only name, folder, status, kind and `access` (`folder_not_open` or `folder_closed`), and can't be read or messaged. `request_access(session)` grants that session's working folder and everything under it, stored for good; `revoke_access(folder)` closes it; `health` lists grants. A built-in deny list (home `.ssh`, `.gnupg`, `.claude`, `.config/claude-voice`, `AppData`, and system folders) is never opened, nor any folder holding one, even inside the root.
- Why: an owner's sessions aren't all under one folder, and the assistant couldn't even tell a session existed. The approval is meant to come from the Claude app's own "ask each time" prompt on `request_access`, which only the person can answer, unlike `approve`, which the assistant calls itself.
- Already shaped for upstream: decision record `decisions/ADR-CV-009-…`, README "How it behaves" and tool table, a `# RULE` ("a folder holding or inside a denied folder is never granted") with its `mutations.toml` row, `tests/test_access.py`, mutation run 5/5.
- Expect discussion: listing names of sessions outside the root shares more with the assistant than today (a side chat's name is the owner's own words); the deny list's contents; upstream's "few tools" principle (two new tools). `SessionManager` takes the deny list as a parameter (empty by default) so tests elsewhere don't hit it; the served bridge passes `default_denied()`.
- Known gap: a side chat outside the open folders isn't marked `side_chat` (the mark is only added to full rows). Grouping side chats under their parent session is deferred.

### 6a. Telling a session who reads it — fork "Tell a session who reads its reply when the relay writes to it"

- What: every message sent with `message_active_session` / `ask_active_session` ends with a short bridge note (`RELAYED_MESSAGE_NOTE`): it comes from the voice assistant, the owner sees neither it nor the reply unless the assistant passes them on, answer in the reply text rather than SendMessage, self-contained. Sessions the bridge starts get the same rules once, appended to their system prompt (`RELAYED_TASK_NOTE`).
- Why: receiving sessions answered with SendMessage into nothing and wrote to the owner about text only the assistant had seen.
- Expect discussion: the notes use this fork's role names ("the relay"); upstream would need its own wording, and maybe a setting. Changing bridge sessions' system prompt is a visible behaviour change.
- Tests: `tests/test_relayed.py`.

### 6b. Project list from Claude Code's history — fork "List the folders Claude Code was used in, wherever they are"

- What: `list_projects` lists the folders Claude Code was used in during the last 30 days, anywhere, read from `~/.claude/projects` (the real folder found from the working folder a conversation records), each marked with `access` when not open; deleted folders left out; search on the folder name.
- Why: the root-only list missed busy folders elsewhere and didn't match the desktop app's sessions.
- Depends on 6 (access marks). Test: `test_the_project_list_includes_folders_claude_was_used_in_anywhere`.

### 6c. peek_session and clearer reader descriptions — fork "Rename the quick question to peek_session and say what each reader sees"

- What: `side_question` renamed `peek_session` (too close to the desktop app's side chats); descriptions of `peek_session`, `digest_session` and `list_sessions` say what each reads (8,000 vs 150,000 characters of the conversation's words, never tool results or files), how fast, and why an idle bridge session isn't "running".
- Expect pushback: renaming a tool breaks clients that remember it. The description part is uncontroversial on its own.

### 6d. The side session, a read-only fork — fork "Ask a read-only fork of a busy session while it keeps working" (ADR-CV-011)

- What: `ask_forked_session(session, question)` forks a running session's conversation through the Agent SDK (`resume` + `fork_session`), one new fork per question, with the owner's settings; a PreToolUse hook refuses every tool outside a reading list (and a matching permission callback), so it changes nothing; returns the answer and tools used.
- Why: keeps a voice conversation going while a session works for a long time, with answers that can draw on the code.
- Follow-up fix, fork "Read past a resumed session's leftover turn, and never call silence an answer": resuming a desktop session first closes a turn left pending in its history (background-task notifications), reported as a result with no turns and no text; the first version took that as the answer. It now skips up to three such turns, and an empty answer or an error result comes back as `status: "error"` with a `detail`. Fold into the same pull request.
- Points to raise: cost per question (each fork carries the whole conversation; the first fork of a desktop session likely misses the prompt cache); forks' conversations stay in Claude Code's history. `# RULE` "the side session never runs a tool that can change anything", `tests/test_fork.py`.

### 6d'. A 20-second cap on every waiting call (ADR-CV-012)

- Since the resync, rebuilt on upstream's per-client time budgets ("Let the side session work on past the caller's time budget", ADR-CV-012 rewritten): the limit is the caller's budget, not a fixed 20 s. What's left to offer is the side session working on past the budget, `next_after` on a send, and the log line for a call the client abandons.
- What: `MAX_WAIT_SECONDS = 20`, applied through one helper (`# RULE` "no tool call waits longer than the cap") to `wait_for_session`, `ask_active_session` and the fork tools; past it `still_working` / `still_running` with a way to read on. Side session as background forks with the same three ways in as open sessions (`message_forked_session`, `read_forked_session`, `ask_forked_session`); `message_active_session` returns `next_after`; a call the client abandons is logged with its duration.
- Why: the Claude app drops a tool call at about 30 s (measured: 28.6 s came back, ~35 s and ~76 s dropped), and the assistant only sees "MCP tool call failed".
- Upstream relevance: high for any claude.ai client. Upstream allows waits of up to 300 s today. The cap value should be a setting; the side-session parts depend on 6d.
- Tests: `tests/test_cap.py`.

### 6d''. Whether an open session's reply is final — fork "Tell the assistant how to read a reply that may not be final"

- Since the resync, upstream has `reply_final` itself; the fork keeps only the manual's lines on reading on ("Tell the assistant how to read a reply that may not be final").
- What: `ask_active_session` returns `reply_final` (`# RULE` "a reply is final only once the turn has ended"); its description and the manual say that a reply under `still_working` is the latest whole message so far, maybe already the answer, and how to read on until the session is idle.
- Why: seen live, the whole answer had been written while the turn was still open at the cap, and the assistant took the result for a status bug.
- Upstream relevance: medium, and higher with a wait cap (6d'). Test: `tests/test_ask.py`.

### 6d'''. Desktop-app sessions reported as "desktop" — fork "Call a session open in the desktop app a desktop session"

- What: `session_kind()` in `tree.py`, used by `fleet_recap` and `session_tree`: a registry entry with `entrypoint: claude-desktop` is "desktop", otherwise "terminal" for `kind: interactive` and "background" for the rest. `list_active_sessions` still shows the raw registry kind.
- Why: the desktop app registers its sessions as interactive, so they were reported as terminal sessions.
- Upstream relevance: high, small and independent. Tests: `tests/test_tree.py`, `tests/test_fleet.py`.

### 6d''''. Titles for every tool and the server — fork "Give every tool and the server a display title"

- Taken upstream before the resync; the fork uses upstream's version now (the fork's commit is on the branch `main-before-resync`).
- What: a `title` on each tool (MCP's display name, e.g. "Check the bridge's health"), and the server's `title` ("Claude voice bridge") and `version` (the one `health` reports) in its initialize answer.
- Why: the fields were empty, so a client had only code names to show, for example in an approval prompt. Whether the Claude app's voice mode shows them is not known yet.
- Upstream relevance: high, standard and independent. Test: `tests/test_titles.py`.

### 6h. Each tool says what it is for in its first line — fork "Say what each tool is for in the first line of its description"

- Offered 2026-11-06 as [#108](https://github.com/roykollensvendsen/claude-voice/pull/108), on upstream's own descriptions (branch `pr/tool-first-lines-2`).
- Reviewed by the author 2026-11-06: first lines checked and right; asked only to keep main's `test_seconds`. Done and pushed, reply posted.
- Second review 2026-11-07: rebase on main, fix `rename_session`'s new first line, keep main's coverage, five first lines that read worse, and a question on plugin tools (answered: on purpose, a plugin's descriptions are its author's). Done 2026-11-08, pushed and answered; waiting for the author.

- What: every tool description opens with one whole sentence of at most 78 characters, the detail on the following lines; `tests/test_surface.py` keeps it so.
- Why: the Claude app shows an assistant each connector tool by the first line of its description, cut at about 79 characters, until the assistant loads the tool. Measured 2026-11-01 in the app's voice mode. Most first lines broke mid-sentence; the manual's never got to say "read this first".
- Upstream relevance: high for any claude.ai client, independent of the rest. Upstream would keep its own words for the lines.

### 6i. A read starts the turn it has no room for — fork "Start the first turn a read has no room for instead of hiding it"

- What: reading a session's turns forward with a text budget, the first turn that doesn't fit is included anyway, cut to the room left (at least 300 characters) and marked cut with its length; the count of turns left covers only what follows.
- Why: a short turn followed by a long reply came back alone with "two turns left", and the assistant reported nothing new while the reply was there. Depends on the budgeted read ("Fill a text budget when a read asks for no count", "Tell the assistant to read on until no turns are left"). Test in `tests/test_live.py`.

### 6j. The log in a file, with token-refresh outcomes — fork "Keep the log in a file, with the outcome of every token refresh"

- Taken upstream before the resync; the fork uses upstream's version now (the fork's commit is on the branch `main-before-resync`).
- What: `configure_logging` writes the log to `bridge.log` next to the database (rotating, 5 MB × 3) as well as the console, uvicorn's request lines included (`log_config=None`); the OAuth provider logs sign-ins, refreshes that worked, refreshes refused (unknown, spent or expired token) and refused access tokens.
- Why: the Claude app gave the connector up after an hour of failed refreshes while the PC slept, and the console log was gone with the restart. Upstream runs under systemd, where journald keeps the console; the token-endpoint lines are useful there too, the file less so (could be behind a setting). Tests in `tests/test_oauth.py`, `tests/test_server.py`.

### 6k. Messages to a session marked not destructive — fork "Stop asking the owner before every message to a session", "Tell the assistant to list every session it can see"

- What: `message_active_session` and `ask_active_session` say `destructive_hint=False` ("Stop asking the owner before every message to a session"), then `stop_active_session` too ("Tell the assistant to list every session it can see", the owner's call: a stopped session can be resumed); `tests/test_server.py` names the one tool marked destructive (`request_access`) and holds every other tool that isn't read-only to an explicit False, since MCP counts a tool that says nothing as destructive. Upstream would likely keep its stop tool destructive; offer "Stop asking the owner before every message to a session" alone.
- Why upstream should want it: the Claude app asks before every call to a destructive tool and offers only "Allow once", even with the connector set to always allow (seen 2026-11-05), so every message in a voice conversation waits for a tap on the phone. A message deletes nothing, and the receiving session keeps its own permissions and approvals (upstream's ADR-CV-003).
- Expect pushback: upstream marked both destructive itself (`caee4a0`, `d9faf6c`); a message can make a session do anything, so the author may see the tap as a safeguard. The answer: the receiving session's own permission rules are the safeguard, and a tap on every message makes the voice loop unusable.
- Test first: the new test was seen failing on `message_active_session` before the markers changed. In upstream the tool names differ for the folder tools (6), so the test's destructive set would be upstream's own.

### 6l. Short default waits, and waiting in silence — fork "Wait 8 seconds by default, before voice mode backgrounds the call", "Wait 5 seconds by default", "Wait for a session's reply by default, and in silence"

- What: `wait_seconds` defaults to 5 s (`WAIT_SECONDS`) on `wait_for_session`, `ask_active_session` and the side-session tools, under the 20 s cap; the manual tells the assistant to wait for a reply by default, say nothing between waits, and answer the owner if they speak, letting the call they talked over return by itself.
- Why: Claude's voice mode moves a call to the background after about 10 s and asks the assistant to say it is taking a while, so a 20 s wait made it speak between every wait. The default was 8 s at first; on 2026-11-06 an 8 s wait from a voice chat, through the owner's server proxy, still went to the background once, while 5 s waits came back in time.
- Upstream relevance: a short default for any claude.ai voice client (depends on 6d'); the manual's wording is this fork's (6e). Test: `tests/test_cap.py`, `tests/test_guide.py`.

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

- What: `request_access(session=None, folder=None)`, exactly one; `folder` is a full path, opened with the same grant, deny list and destructive marker as a session's folder. README and ADR-CV-009 updated.
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

### 6t. Renaming a session the bridge started in Claude Code itself — fork "Rename a session the bridge started", "Write a new name in the compact form Claude Code uses", "Rename a session through its job record, where Claude Code looks", "Rename a session the bridge started in Claude Code itself"

- Since the resync of 2026-11-08, upstream has its own `rename_session`, for any running session, renamed in Claude Code by typing into tmux; the fork merged the two (resync 2026-11-08, choice 3). What's left to offer: the job record as a way to rename a background session in Claude Code, on any platform, beside tmux. The text below is the fork's first version, before the merge.
- What: `rename_session(session, name)` writes the new name into Claude Code's job record for that background session (`~/.claude/jobs/<id>/state.json`: `name`, `nameSource: "user"`, and the `--name` in `respawnFlags`); a running session watches that record and renames itself within seconds, and a wake keeps it. Sessions the bridge started only (`# RULE`), never mid-turn. The first two commits wrote to the conversation instead, which didn't reach the session lists; fold all three into one pull request as the last one's shape.
- Why: the assistant names a session at start and could never change its mind.
- Expect pushback: it writes Claude Code's internal state, an undocumented format found by reading Claude Code's code (`jobStateNameSync`), which may change with any release. Tests: `tests/test_rename.py`.

### 6u. A news line on every result, replacing whats_new — fork "Tell the relay in every result what it hasn't read yet", "Remove whats_new now that every result carries the news"

- What: each tool result for an MCP session can start with `news`, one line naming the sessions that conversation started, resumed, messaged or read that have turns past its read marker (a relayed message never counts; turns the owner typed are counted as such), with their state (finished, still working, waiting, ended), and any session waiting on a permission prompt. Markers live in memory, per `mcp-session-id`. `whats_new` and its cursor are removed ("Remove whats_new now that every result carries the news"); the watcher stays for the wake at start (6s).
- Why: a polling tool nobody called; the relay kept missing replies, most of all when the owner wrote to a session directly.
- Later fix ("Keep a reply as news until the relay reads it, even after another send"): only a read marks a reply read, not a send, and a reply followed by a later message counts as ended even while the session works again. Seen live: the relay sent several messages without reading in between, a report ended between two of them, and every send said "nothing new".
- Expect pushback: an extra field on every result, and a removed tool; depends on 6n's per-conversation identity and on transcript `origin` entries. Test first: `tests/test_news.py`, over HTTP.

### 6v. A finished turn's last message whole — fork "Return a finished turn's last message whole"

- What: once a turn has ended, `wait_for_session`, `ask_active_session` and `read_session_output` return its last message whole up to `FINAL_CHARS` (12,000), with `reply_cut`/`reply_length` (or `cut`/`length`) past that; other turns keep `max_chars`.
- Why: final reports of 5,000 to 6,000 characters needed two or three more reads. Small and independent; offer early. Test first: `tests/test_final_reply.py`.

### 6w. Briefs written by the session that holds the context — fork "Have the session that holds the context write a new brief"

- What: one line in the manual's relay scope. Fork-specific wording; only with 6e.

### 6x. Starting sessions in the background on Remote Control — fork "Start new work as background sessions, a window only when asked", "Resume earlier conversations as background sessions", "Continue a former background session instead of copying it"

- Status: an idea for later (owner, 2026-11-06); nothing prepared, no branch.
- What: a session the bridge starts, or resumes from an earlier conversation, runs as an ordinary Claude Code background session with Remote Control on, with no window; the owner opens it from the phone or the desktop app.
- Why upstream should want it: upstream's own commit `248f405` (2026-11-02) opens a terminal window for the same reason, sessions the phone couldn't reach. The background way needs no desktop, no terminal program and no tmux, and works the same on Windows, macOS and Linux.
- Expect pushback: upstream just built the window, with settings and a companion-app page for it. Offer it as one more launch mode (`launch = "remote_control"` or similar) next to theirs, not as a replacement. Depends on how upstream's start is shaped after the resync; rebuild it on their launch settings.
- Test first: the fork's start tests (`tests/test_start.py`), rewritten against upstream's launch settings.

### 6y. Updating the bridge on Windows — fork "Update the bridge on Windows, where there is no systemd"

- What: `update_bridge` works without systemd. On Windows the bridge starts the updater as a detached process, and the updater restarts the bridge by ending it with exit code 75 (`RESTART_EXIT`), which the script that runs the bridge takes as "start me again"; a rollback does the same. The updater also checks health on the address the bridge listens on (`--host`), not always 127.0.0.1, which helps a Linux bridge bound to a Tailscale or WireGuard address too.
- Why: upstream's update relies on `systemd-run` and `systemctl`, so on Windows (and macOS) it fails at the start.
- Expect pushback: the restart needs the launcher's cooperation (a loop on exit code 75), which upstream doesn't ship for Windows; it comes with 8 (Windows in the README and CI). Tests: `tests/test_update.py`, the three Windows tests.

### 6z. Trusting a new folder on Windows — fork "Trust a new folder under the name Claude Code looks it up by"

- What: `trust_folder`, which marks a folder `start_active_session` has just made as trusted in Claude Code's config, writes its key with forward slashes (`folder.as_posix()`), as Claude Code keys a Windows folder; with the backslashed `str(folder)`, Claude Code never read it and refused the start ("Workspace not trusted"). Found in the resync's trial run.
- Why: a small bug fix on Windows; no change on Linux or macOS, where both forms are the same. Offer with 8, or alone. Test first: `tests/test_start.py`, with a Windows path, so it fails on Linux CI too.

### 6g. Handing a session to the desktop app — fork "Hand a session the bridge started to the desktop app"

- What: `hand_to_desktop(name)` stops a session `start_active_session` started and opens a named Windows Terminal tab running `claude --desktop --resume '<id>'`, which brings it into the Claude desktop app's sidebar. Refuses sessions the bridge didn't start, mid-turn ones and ones with no conversation yet. A Remote Control hook (`REMOTE_CONTROL_AT_START`, off) for `claude --bg --remote-control`.
- Why: sessions the bridge starts are otherwise invisible in the desktop app.
- Upstream relevance: medium; Windows-only as written (Windows Terminal, PowerShell), macOS would need its own terminal. Not tried live yet. Tests: `tests/test_handover.py`.

### 6e. A manual for the assistant — fork "Give the bridge a manual, sent at connection and kept in a guide tool", cut down in "Keep in the manual only the rules that span tools"

- What: the server instructions rewritten as a short manual (roles, the rule that the owner sees nothing passing through tools, keeping agents' words apart, the relay's scope, how to wait, what only the owner answers), and a `guide` tool returning the same text for clients that drop server instructions. Since "Keep in the manual only the rules that span tools" it holds only what spans tools; what one tool needs is in that tool's description (6h).
- Expect discussion: the manual is written around this fork's voice relay naming; upstream would take the structure and the guide tool, and keep its own words. Test: `tests/test_guide.py`.

### 6f. "List every session" instruction and the relay label — fork "Tell the assistant to list every session it can see"

- What: one paragraph telling the assistant to name every session from every source when asked. Folded into 6e's manual; only worth offering as part of it.

### 7. Serving under a path prefix — not in the fork yet

- What: the bridge works behind a reverse proxy under a path (`CLAUDE_VOICE_PUBLIC_URL=https://host/claude-voice`) only because Nginx also forwards three root paths. One of them is the bridge's own doing: the consent form posts to an absolute `/oauth/consent` (`src/claude_voice/oauth.py`, `_consent_form`). A relative action (`action='consent'`) would remove it. The other two are where OAuth clients look for metadata (RFC 9728, RFC 8414) and can't be avoided by the bridge alone; document them.
- Why: upstream documents Tailscale Funnel only; a prefix behind an existing reverse proxy is a common setup.
- Would need: the change, a test that the form's action stays under the public URL, and a README section on running behind a reverse proxy.

### 8. Windows in the README and CI — prerequisite for 1–3

- Upstream says "Linux or macOS". Supporting Windows means a decision record (scope: the bridge runs on Windows; the systemd service and `install.sh` stay Linux-only; Windows runs it by hand or as a startup task), a README section, and a Windows job in `.github/workflows/checks.yml` so it stays working (today every job is `ubuntu-latest`).
- Without the CI job, Windows support would rot; upstream's own rule ("every rule is enforced by something that can fail") argues for it.
- Best offered first as an issue, asking whether Windows is wanted at all, before any pull request.

### 0. A test that read the shell's environment — fork "Clear the courier name from the environment in the hook test"

- Taken upstream before the resync; the fork uses upstream's version now (the fork's commit is on the branch `main-before-resync`).
- What: `test_the_configured_courier_name_is_ignored_even_after_the_courier_is_gone` clears `CLAUDE_VOICE_COURIER_NAME` first; the hook reads the environment before the settings file, so the test failed in any shell where the bridge's variable was set. Trivial, independent, offer first.

## Not for upstream

- The owner's setup around the fork (Windows service scripts, the server proxy, its values and working notes), kept in a private repository: specific to that setup.
- The fork's `CLAUDE.md` (fork "Check how far behind upstream the fork is at each session start"): each session fetches upstream and offers the owner a resync when the fork is behind, the last sync being the merge base of `main` and `upstream/main`. Merged with upstream's own `CLAUDE.md` at the resync: upstream's pointers first, then this rule under "The owner's fork". The same section (fork "Say the fork is AI-managed") says the fork is AI-managed, maintained by kelno's own agents under kelno's private rules, and that for those agents the gates and the ratchet run as slow checks rather than before every push.
- The guide's line saying the bridge is the owner's own project, so a change to it is a fair suggestion when a tool gets in the way (fork "Tell the relay the bridge is the owner's own project", then "Call the change feedback names a lead, not the fix"): true only of this fork.
- The commit-msg hook calls `python3`, which the python.org installer doesn't create on Windows (only `python.exe`), so the hook fails there unless something else provides `python3`. Fixed on this PC outside the repo, with a `python3.exe` link. Worth a one-line issue at most, not a fork change.

## Still open in the fork

- The session tree (`tree.py`, `SOCKET_PID`) links sessions that message each other through a process id read from Unix socket names. Windows uses named pipes (`\\.\pipe\LOCAL\cc-msg-…`, no pid). Name matching already works; the fallback needs the real Windows address format, now observable since messaging works. Would join candidate 8.
