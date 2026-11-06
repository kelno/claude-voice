# Upstreaming the Windows fork

What in this fork, [kelno/claude-voice](https://github.com/kelno/claude-voice), could go back to [claude-voice](https://github.com/roykollensvendsen/claude-voice) by Roy Kollen Svendsen as pull requests, how the fork differs from it and why, and the decisions taken when bringing the two back together. Written to be picked up later, cold, by people and agents.

- "The owner" is kelno, who maintains this fork; "the relay" is the voice assistant in the Claude app that talks to the bridge for them; "agents" are the Claude sessions that do the work.
- The fork split from upstream at `21d1d96` (2026-10-29). Upstream moves fast; check what it has taken before offering anything.
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

1. The tests the change touches, `ruff check`, `ruff format --check` and `mypy src`; each new test seen failing at the `test:` commit.
2. `uv run python scripts/ratchet.py update --skip-mutation`, with `quality.json` committed in the `feat:` or `fix:` commit (it runs the whole suite once, about 90 s).
3. Push the branch to the fork (`origin`, push URL `github-agents:`), never to `upstream`. The session opens the pull request itself, as a draft (`gh pr create --draft --repo roykollensvendsen/claude-voice`), and the owner reviews it, edits it and marks it ready. Its text opens with a disclaimer saying an AI wrote it on kelno's behalf, and doesn't speak as the owner. If CI then reports a mutation that survived, fix it and push again.
4. From the resync on, this is done in upstream's contribute session (`start_role`), set up as in resync decision 7 (owner, 2026-11-06). Opening the draft needs the GitHub command-line tool (`gh`) installed and signed in with the agents' GitHub account; until then, the session pushes the branch and the owner opens the pull request.

## Resync decisions (2026-11-06)

The fork will be rebuilt on top of upstream's `main` (132 commits behind, 69 of its own at the time), replaying fork commits one at a time. The owner's choices, one per point where the two diverged:

1. Wait limits: take upstream's per-client time budgets (one limit per tool call: 20 s for a call from another computer, counted as the Claude app; 2 min for upstream's voice app; 60 s otherwise), and keep the fork's 8 s default wait on top. Condition: a test that the voice relay's calls, which come through the owner's server proxy, get the Claude app's 20 s; if the bridge sees them as local, pin them to 20 s.
The owner's general rule: align with upstream, refactoring the fork to match it, as long as every fork feature is kept.

2. Tool titles: settled by the rule. Upstream's shared titles table, with the fork's own tools added to it.
3. Overlapping tools: settled. `digest_session` becomes upstream's, with anything the fork's does on top. Upstream's `side_question` is the fork's `peek_session` under its old name, unchanged (same code; upstream's only change is where its title is stored), so the fork keeps `peek_session` and doesn't take `side_question`. The side session (`ask_forked_session`, `message_forked_session`, `read_forked_session`) stays, for digging deeper (owner, 2026-11-06).
4. Starting sessions: settled (owner, 2026-11-06). The fork's way stays the default: a session started by voice runs in the background on Remote Control. Upstream's terminal window is kept as an optional setting for one start. Why: upstream's window solves the problem the fork already solved (sessions out of reach from the phone); the owner finds the fork's way cleaner, and upstream's window works only on Linux unless a terminal is named in its settings. Offered upstream as candidate 6x.
5. Folder trust: settled by the rule. Upstream's trust of folders it made, plus the fork's trust of folders opened by path.
6. Message tools marked destructive: settled by the rule, the fork's choice is a feature. Messages stay unmarked, so the Claude app doesn't ask for a tap on each one.
7. Upstream-only features (owner, 2026-11-06): all the code comes in; what's switched on:
   - Self-update (`update_changes`, `update_bridge`): on. It pulls the branch the bridge's clone follows, which for the fork is the fork's `main` on GitHub, never upstream, and only as a fast-forward; it refuses to start with local changes, runs the tests, and rolls back on failure, so it can't overwrite fork changes. It doesn't bring upstream's changes: those still come by a resync. What an update brings is read from the commit messages, grouped by kind, not from release notes. Needs a Windows version: it restarts the bridge through `systemd-run`, which this PC doesn't have, and its full test run is heavy here.
   - Contribute role (`start_role`): likely on. Needs the role set up for this PC in `~/.config/claude-voice/roles.toml`: its folder on a separate worktree, not the clone the bridge runs from; its first message following "How we check a pull request here" above (no full mutation sweep, push through `github-agents:`, open the pull request as a draft with `gh` for the owner to review and mark ready), since upstream's asks it to run the whole mutation script and open ready pull requests. Needs `gh` installed and signed in with the agents' account.
   - Chosen session (`get_active_session`, `set_active_session`): off.
   - Companion app: off.
   - Plugins: code in, off; no other MCP servers on this PC for now.

## Differences from upstream

Every way the fork differs from upstream's `main`, by what an agent calling the tools or the owner sees, with why and the fork commits. Built from the fork's history on 2026-11-06 (fork `d6f666d`, upstream `1339a46`); keep it current with each fork commit and each resync. Nothing here is a guess unless marked so. The candidate entries below have the detail for what could go upstream.

### Renamed

| Upstream name | Fork name | Why | Fork commit |
| --- | --- | --- | --- |
| `side_question` | `peek_session` | Says it's a quick look at what the session wrote, next to the side session that can read code; same code otherwise | `7e5ddb1` |
| `wait_for_session` (waited for a session the bridge ran itself) | `wait_for_session` (waits for any running session's turn to end) | Same name, a different tool: the old one went with the in-bridge sessions, the new one is offered upstream as a pull request | `0f78906`, `e429ece` |

### Tools only in the fork

| Tool | What it's for | Why | Fork commits |
| --- | --- | --- | --- |
| `ask_forked_session`, `message_forked_session`, `read_forked_session` | The side session: a read-only copy of a busy session's conversation that answers a question and can read the code | Dig deeper into a session that's working, without disturbing it | `f7931ab`, `644c709`, `2bf9324` |
| `guide` | The manual for the calling agent; also handed to a conversation that never read it | The Claude app doesn't pass the server's instructions to the model | `6c22d09`, `443c172`, `0344db1`, `26651d6` |
| `hand_to_desktop` | Hands a session the bridge started to the desktop app | Carry on at the PC | `d5cb737` |
| `rename_session` | Renames a session the bridge started | Sessions named by what they do | `7b97838`, `8322c03`, `639d09d` |
| `request_access`, `revoke_access` | Open a folder outside the root, by session or by path, and close it again; a folder opened by path is also trusted by Claude Code | Work anywhere on the PC, one folder at a time, with the owner's approval | `149a154`, `ee764a2`, `735f856`, `1093656` |

### Upstream tools the fork removed

| Tools | Why | Fork commits |
| --- | --- | --- |
| `send_task`, `session_recap`, `cancel`, `close_session`, `list_sessions`, `create_session`, `approve`, `deny`, `list_pending_approvals` | They served sessions run inside the bridge, which the owner couldn't open on the phone or at the PC and whose prompts came only through the relay; all work now runs as ordinary sessions on Remote Control | `4e09bfe`, `54250cc`, `0f78906` |
| `whats_new` | No relay thought to call it; every result now carries a line on what the relay hasn't read | `68b1a07`, `5ae1dd2` |

### Upstream tools the fork doesn't have yet

Added upstream after the fork split off; resync choice 7 decides which are switched on: `check_dependencies`, `get_active_session`, `set_active_session`, `refresh_session_summary`, `start_role`, `update_bridge`, `update_changes`.

### Same tools, different behaviour

| What | Upstream | Fork | Why | Fork commits |
| --- | --- | --- | --- | --- |
| How long a call waits | A limit per client (20 s for the Claude app, 2 min for its voice app, 60 s otherwise) | 20 s for every call, 8 s by default | The Claude app drops a call at about 30 s, and its voice mode speaks up after about 10 s (resync choice 1: take upstream's limits, keep the 8 s default) | `2bf9324`, `c1ee5f3`, `ce84de4` |
| Approval taps on messages | `message_active_session`, `ask_active_session` and `stop_active_session` marked destructive, so the Claude app asks for a tap on each | Not marked destructive | A tap on every message makes the voice loop unusable; the receiving session keeps its own permissions | `dcbb5c4`, `b3d3f1c` |
| Starting a session | In a terminal window or in the background, as the settings say | Only in the background, on Remote Control, on Opus unless a model is asked for | Sessions the owner can open on the phone and at the PC; Opus for everyday work | `4e09bfe`, `968f85b`, `88a5c8e`, `f8f6f02` |
| Continuing an earlier conversation (`attach_conversation`) | A copy run inside the bridge | Resumed as a background session on Remote Control | Same as above | `54250cc`, `f75e928`, `e2ef4cd` |
| Reading a session (`read_session_output`) | Six turns by default, a `latest` field when far behind | Fills a text budget; the first turn that doesn't fit is cut and shown rather than hidden | A long reply after a short turn came back as "nothing new" | `1875280`, `549ed54`, `ec06021` |
| Every result | Nothing added | A line on what the relay hasn't read yet, and notes on late results and on waits still running | The relay missed news, and copied the app's late-result calls | `68b1a07`, `dc25e29`, `90db04a` |
| Session lists | Terminal or background | Desktop-app sessions shown as "desktop", side chats marked | The owner uses the desktop app | `70746bf`, `dd11c18` |
| Project list (`list_projects`) | Folders under the root | Every folder Claude Code was used in, wherever it is | The owner's projects aren't under one root | `43416da` |
| Messages written into a session | As sent | Say that the relay reads the reply | So the session answers an agent, self-contained | `2d741aa` |
| Bridge start | Nothing | Wakes the sessions a shutdown interrupted | The PC sleeps and restarts | `ffdd67f`, `07b58bd` |
| Display titles | One shared table | Titles in the code | Resync choice 2: take upstream's table | `48ac885` |
| First line of each description | Runs past the Claude app's cut | One sentence within 78 characters | The app shows only that line until a tool loads; offered upstream as a pull request | `2ce310c` |
| Wording | "background" sessions | "Remote Control" sessions | Agents told the owner sessions were hidden when they were on the phone | `db26ff1` |

## Most promising for upstream (ideas, 2026-11-06)

Beyond the two pull requests already prepared (`wait_for_session`, the short first lines), the fork features most likely to help upstream, most promising first. Ideas only: nothing prepared. Each points to its candidate entry below.

0. The relay waits on a session and carries on by itself (owner, 2026-11-06: "a big improvement over upstream"). The pieces: `wait_for_session` with `after` (in the `pr/wait-for-session` branch); `message_active_session` returning `next_after`, so a send hands the wait its starting point (6d', not in the branch; upstream's send returns none, so the relay has to read first); the 8-second default wait, under voice mode's ~10-second move to the background (6l, not in the branch, which waits up to the client's 20-second budget); the manual's "Waiting" rules, wait again on "still_working", in silence, answer the owner if they speak (6l, 6e, not in the branch); the `pending` note on a session already being waited on (6m, not in the branch). The move to the background itself is the Claude app's, not the bridge's. Guess: the news line on every result (`68b1a07`) helps the relay notice replies but isn't what makes it wait. Offer as a follow-up to `wait_for_session`: first `next_after` on a send, then the short default and the waiting rules.
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

### 1. Liveness without signals — fork `7d7df28`

- What: whether a session's process is alive is asked through `psutil.pid_exists` (new `procs.py`), not `os.kill(pid, 0)`.
- Why upstream should want it: on Windows `os.kill(pid, 0)` ends the process, so the bridge closes every Claude Code session it checks, every 5 seconds. Harmless on Linux and macOS either way, so it changes nothing for current users.
- Cost: a new dependency, psutil (and types-psutil for mypy). Upstream may prefer a tiny platform switch (keep `os.kill` on POSIX, `OpenProcess` through ctypes on Windows) to avoid a dependency; offer both.
- Test: `tests/test_liveness.py` fails if the check sends any signal. It is red on Linux too before the fix, so upstream's Linux-only CI can see it.
- Likely ask: a decision record on supporting Windows at all (see "Prerequisite" below), since this is only worth it if Windows is in scope.

### 2. Memory figure without /proc — fork `f04db5a`

- What: `health`'s memory figure from psutil instead of `/proc/self/status`.
- Why: it reads zero on macOS today too, not only Windows. Small, depends on 1 for psutil.
- Test: the existing health test; on Linux it passes before and after, so the red step is only visible on macOS or Windows. Could be folded into 1.

### 3. UTF-8 for every text file — fork `5de3c2f`

- What: every `read_text`, `write_text` and text-mode `open` in src, the hook, scripts and tests names `encoding="utf-8"`.
- Why: Claude Code writes its transcripts in UTF-8; without an explicit encoding Python uses the system's, which is cp1252 on Windows, and reading a session's output crashed on the first curly quote. Upstream's own test tooling hits it too. Also protects Linux machines with a non-UTF-8 locale.
- Test: `test_a_transcript_is_read_as_utf8_whatever_the_system_encoding`. Problem for upstream: on Linux CI it passes before the fix (UTF-8 locale), so the red step isn't visible there. Options: run the test under a forced legacy encoding (for example by monkeypatching `locale.getpreferredencoding` and the `io.text_encoding` path), or add a ruff rule that refuses `read_text()` without an encoding (`PLW1514`, unspecified-encoding) as the mechanism. The ruff rule fits upstream's "every rule is enforced by something" best.
- Independent of 1 and 2; the easiest to accept.

### 4. Waiting for a session — fork `c721c3c`

- What: tool `wait_for_session(session_id, wait_seconds=60)`, up to 300 s, returning the recap with `waited` = finished, needs_approval, still_running, not_running. The instructions to the assistant use it after `send_task`.
- Why: a voice client can't be notified, so it polled `session_recap` and the person had to remind it to look; in a real voice test the assistant asked for exactly this.
- Expect pushback: upstream keeps the tool surface small (`tests/test_surface.py`). The alternative it may prefer: a `wait_seconds` parameter on `session_recap` instead of a new tool. Offer that shape first.
- Unknown worth settling first: how long claude.ai waits for one tool call before giving up. Measure before proposing a default.
- Follow-up fix, fork `adba52a`: a task that ended before the wait began reported "not_running"; now "finished" when the session was ever given a task. Fold into the same pull request.
- Test: `tests/test_wait.py`.

### 5. Side chats marked — fork `dd11c18`

- What: running sessions whose name starts with "btw:" carry `side_chat: true` in `list_active_sessions` and `fleet_recap`; the instructions say not to send work there unless asked.
- Why: the Claude desktop app (macOS too, so not Windows-specific) runs a side chat as its own session, and the assistant can't tell it from a project session.
- Weakness: it relies on the app's naming, not a field (the session registry has none). Say so in the pull request; upstream may prefer to wait for a real field from Claude Code.
- Test: `test_a_desktop_side_chat_is_marked_as_one`.

### 6. Folders outside the root, opened by the owner — fork `149a154`

- Follow-up, fork `ee764a2` (ADR-CV-007): values renamed `folder_not_open` / `folder_closed` (the first collided with tool-call approvals), refusals name the closed folder and the relation (inside / holds), and an exact-depth exception opens the Claude desktop app's scratch folders under `AppData` (`# RULE` "only a Desktop scratch folder is opened inside a denied folder"). The exception is Windows-desktop specific; upstream may want it behind a setting.
- What: every running session is listed; one outside the root and the granted folders shows only name, folder, status, kind and `access` (`folder_not_open` or `folder_closed`), and can't be read or messaged. `request_access(session)` grants that session's working folder and everything under it, stored for good; `revoke_access(folder)` closes it; `health` lists grants. A built-in deny list (home `.ssh`, `.gnupg`, `.claude`, `.config/claude-voice`, `AppData`, and system folders) is never opened, nor any folder holding one, even inside the root.
- Why: an owner's sessions aren't all under one folder, and the assistant couldn't even tell a session existed. The approval is meant to come from the Claude app's own "ask each time" prompt on `request_access`, which only the person can answer, unlike `approve`, which the assistant calls itself.
- Already shaped for upstream: decision record `decisions/ADR-CV-006-…`, README "How it behaves" and tool table, a `# RULE` ("a folder holding or inside a denied folder is never granted") with its `mutations.toml` row, `tests/test_access.py`, mutation run 5/5.
- Expect discussion: listing names of sessions outside the root shares more with the assistant than today (a side chat's name is the owner's own words); the deny list's contents; upstream's "few tools" principle (two new tools). `SessionManager` takes the deny list as a parameter (empty by default) so tests elsewhere don't hit it; the served bridge passes `default_denied()`.
- Known gap: a side chat outside the open folders isn't marked `side_chat` (the mark is only added to full rows). Grouping side chats under their parent session is deferred.

### 6a. Telling a session who reads it — fork `2d741aa`

- What: every message sent with `message_active_session` / `ask_active_session` ends with a short bridge note (`RELAYED_MESSAGE_NOTE`): it comes from the voice assistant, the owner sees neither it nor the reply unless the assistant passes them on, answer in the reply text rather than SendMessage, self-contained. Sessions the bridge starts get the same rules once, appended to their system prompt (`RELAYED_TASK_NOTE`).
- Why: receiving sessions answered with SendMessage into nothing and wrote to the owner about text only the assistant had seen.
- Expect discussion: the notes use this fork's role names ("the relay"); upstream would need its own wording, and maybe a setting. Changing bridge sessions' system prompt is a visible behaviour change.
- Tests: `tests/test_relayed.py`.

### 6b. Project list from Claude Code's history — fork `43416da`

- What: `list_projects` lists the folders Claude Code was used in during the last 30 days, anywhere, read from `~/.claude/projects` (the real folder found from the working folder a conversation records), each marked with `access` when not open; deleted folders left out; search on the folder name.
- Why: the root-only list missed busy folders elsewhere and didn't match the desktop app's sessions.
- Depends on 6 (access marks). Test: `test_the_project_list_includes_folders_claude_was_used_in_anywhere`.

### 6c. peek_session and clearer reader descriptions — fork `7e5ddb1`

- What: `side_question` renamed `peek_session` (too close to the desktop app's side chats); descriptions of `peek_session`, `digest_session` and `list_sessions` say what each reads (8,000 vs 150,000 characters of the conversation's words, never tool results or files), how fast, and why an idle bridge session isn't "running".
- Expect pushback: renaming a tool breaks clients that remember it. The description part is uncontroversial on its own.

### 6d. The side session, a read-only fork — fork `f7931ab` (ADR-CV-008)

- What: `ask_forked_session(session, question)` forks a running session's conversation through the Agent SDK (`resume` + `fork_session`), one new fork per question, with the owner's settings; a PreToolUse hook refuses every tool outside a reading list (and a matching permission callback), so it changes nothing; returns the answer and tools used.
- Why: keeps a voice conversation going while a session works for a long time, with answers that can draw on the code.
- Follow-up fix, fork `644c709`: resuming a desktop session first closes a turn left pending in its history (background-task notifications), reported as a result with no turns and no text; the first version took that as the answer. It now skips up to three such turns, and an empty answer or an error result comes back as `status: "error"` with a `detail`. Fold into the same pull request.
- Points to raise: cost per question (each fork carries the whole conversation; the first fork of a desktop session likely misses the prompt cache); forks' conversations stay in Claude Code's history. `# RULE` "the side session never runs a tool that can change anything", `tests/test_fork.py`.

### 6d'. A 20-second cap on every waiting call (ADR-CV-009)

- What: `MAX_WAIT_SECONDS = 20`, applied through one helper (`# RULE` "no tool call waits longer than the cap") to `wait_for_session`, `ask_active_session` and the fork tools; past it `still_working` / `still_running` with a way to read on. Side session as background forks with the same three ways in as open sessions (`message_forked_session`, `read_forked_session`, `ask_forked_session`); `message_active_session` returns `next_after`; a call the client abandons is logged with its duration.
- Why: the Claude app drops a tool call at about 30 s (measured: 28.6 s came back, ~35 s and ~76 s dropped), and the assistant only sees "MCP tool call failed".
- Upstream relevance: high for any claude.ai client. Upstream allows waits of up to 300 s today. The cap value should be a setting; the side-session parts depend on 6d.
- Tests: `tests/test_cap.py`.

### 6d''. Whether an open session's reply is final — fork `9f4cad9`

- What: `ask_active_session` returns `reply_final` (`# RULE` "a reply is final only once the turn has ended"); its description and the manual say that a reply under `still_working` is the latest whole message so far, maybe already the answer, and how to read on until the session is idle.
- Why: seen live, the whole answer had been written while the turn was still open at the cap, and the assistant took the result for a status bug.
- Upstream relevance: medium, and higher with a wait cap (6d'). Test: `tests/test_ask.py`.

### 6d'''. Desktop-app sessions reported as "desktop" — fork `70746bf`

- What: `session_kind()` in `tree.py`, used by `fleet_recap` and `session_tree`: a registry entry with `entrypoint: claude-desktop` is "desktop", otherwise "terminal" for `kind: interactive` and "background" for the rest. `list_active_sessions` still shows the raw registry kind.
- Why: the desktop app registers its sessions as interactive, so they were reported as terminal sessions.
- Upstream relevance: high, small and independent. Tests: `tests/test_tree.py`, `tests/test_fleet.py`.

### 6d''''. Titles for every tool and the server — fork `48ac885`

- What: a `title` on each tool (MCP's display name, e.g. "Check the bridge's health"), and the server's `title` ("Claude voice bridge") and `version` (the one `health` reports) in its initialize answer.
- Why: the fields were empty, so a client had only code names to show, for example in an approval prompt. Whether the Claude app's voice mode shows them is not known yet.
- Upstream relevance: high, standard and independent. Test: `tests/test_titles.py`.

### 6h. Each tool says what it is for in its first line — fork `2ce310c`

- What: every tool description opens with one whole sentence of at most 78 characters, the detail on the following lines; `tests/test_surface.py` keeps it so.
- Why: the Claude app shows an assistant each connector tool by the first line of its description, cut at about 79 characters, until the assistant loads the tool. Measured 2026-11-01 in the app's voice mode. Most first lines broke mid-sentence; the manual's never got to say "read this first".
- Upstream relevance: high for any claude.ai client, independent of the rest. Upstream would keep its own words for the lines.

### 6i. A read starts the turn it has no room for — fork `ec06021`

- What: reading a session's turns forward with a text budget, the first turn that doesn't fit is included anyway, cut to the room left (at least 300 characters) and marked cut with its length; the count of turns left covers only what follows.
- Why: a short turn followed by a long reply came back alone with "two turns left", and the assistant reported nothing new while the reply was there. Depends on the budgeted read (`1875280`, `549ed54`). Test in `tests/test_live.py`.

### 6j. The log in a file, with token-refresh outcomes — fork `70d2887`

- What: `configure_logging` writes the log to `bridge.log` next to the database (rotating, 5 MB × 3) as well as the console, uvicorn's request lines included (`log_config=None`); the OAuth provider logs sign-ins, refreshes that worked, refreshes refused (unknown, spent or expired token) and refused access tokens.
- Why: the Claude app gave the connector up after an hour of failed refreshes while the PC slept, and the console log was gone with the restart. Upstream runs under systemd, where journald keeps the console; the token-endpoint lines are useful there too, the file less so (could be behind a setting). Tests in `tests/test_oauth.py`, `tests/test_server.py`.

### 6k. Messages to a session marked not destructive — fork `dcbb5c4`, `b3f5634`

- What: `message_active_session` and `ask_active_session` say `destructive_hint=False` (`dcbb5c4`), then `stop_active_session` too (`b3f5634`, the owner's call: a stopped session can be resumed); `tests/test_server.py` names the one tool marked destructive (`request_access`) and holds every other tool that isn't read-only to an explicit False, since MCP counts a tool that says nothing as destructive. Upstream would likely keep its stop tool destructive; offer `dcbb5c4` alone.
- Why upstream should want it: the Claude app asks before every call to a destructive tool and offers only "Allow once", even with the connector set to always allow (seen 2026-11-05), so every message in a voice conversation waits for a tap on the phone. A message deletes nothing, and the receiving session keeps its own permissions and approvals (upstream's ADR-CV-003).
- Expect pushback: upstream marked both destructive itself (`caee4a0`, `d9faf6c`); a message can make a session do anything, so the author may see the tap as a safeguard. The answer: the receiving session's own permission rules are the safeguard, and a tap on every message makes the voice loop unusable.
- Test first: the new test was seen failing on `message_active_session` before the markers changed. In upstream the tool names differ for the folder tools (6), so the test's destructive set would be upstream's own.

### 6l. Short default waits, and waiting in silence — fork `c1ee5f3`, `ce84de4`

- What: `wait_seconds` defaults to 8 s (`WAIT_SECONDS`) on `wait_for_session`, `ask_active_session` and the side-session tools, under the 20 s cap; the manual tells the assistant to wait for a reply by default, say nothing between waits, and answer the owner if they speak, letting the call they talked over return by itself.
- Why: Claude's voice mode moves a call to the background after about 10 s and asks the assistant to say it is taking a while, so a 20 s wait made it speak between every wait.
- Upstream relevance: the 8 s default for any claude.ai voice client (depends on 6d'); the manual's wording is this fork's (6e). Test: `tests/test_cap.py`, `tests/test_guide.py`.

### 6m. Notes on late results — fork `dc25e29`, `90db04a`

- What: a middleware (`result_notes()`) adds `late` to the result of any call that took 1 s or more (the app may deliver it late, inside a call carrying `_deferred_result_for`, which only the app writes), and `pending` to a call on a session while a wait or ask on it still runs.
- Why: voice mode delivers a backgrounded result as a call the assistant seems to have written, and assistants copy it to fetch results, refused each time; the note sits in the very result they copy.
- Expect pushback: it names a field of one client's internals in every slow result; upstream may want it behind a setting. It also rewrites the result's text content from its structured content. Tests: `tests/test_cap.py`.

### 6n. The manual handed to a conversation that never read it — fork `0344db1`

- What: `result_notes()` remembers, by the `mcp-session-id` header, which MCP sessions have had the manual (the newest 1,000); the first result of a session that never called `guide` carries the manual itself, first in the result. A client without an MCP session (stateless, or the 2026-07-28 single-exchange protocol) gets nothing, so it isn't handed the manual on every call.
- Why upstream should want it: the Claude app opens one MCP session per conversation but doesn't pass server instructions on to the model, and its agents load tools by search, so `guide` is easily never called (seen for a whole evening). Upstream has the same instructions and would face the same.
- Expect pushback: about 3,500 characters of context once per conversation; it depends on the app keeping one MCP session per conversation (seen in this fork's log: about one initialize per conversation, hundreds of calls each), and would deliver nothing if the app moves to a sessionless protocol. Depends on 6e and 6m. Test first: `tests/test_server.py`, over HTTP with an initialize handshake, seen failing before the change.

### 6o. request_access by folder path — fork `735f856`

- What: `request_access(session=None, folder=None)`, exactly one; `folder` is a full path, opened with the same grant, deny list and destructive marker as a session's folder. README and ADR-CV-006 updated.
- Why: to start work remotely in a folder no session runs in, which the session-only form could never open. Depends on 6.
- Expect pushback: a path typed by the assistant rather than a folder the owner already works in; the answer is the app's own prompt on the destructive tool, which shows the path and offers no "Always allow". Test first: `tests/test_access.py`, seen failing before the change.

### 6o'. Claude Code's trust with a folder opened by path — fork `1093656`

- What: `request_access(folder=...)` also sets `projects["<path>"].hasTrustDialogAccepted` in Claude Code's config file (`~/.claude.json`, or under `CLAUDE_CONFIG_DIR`), keyed on the git repository's root when inside one, as Claude Code's permissions docs describe for trusting a folder by hand. Other keys are kept and the file is swapped in one step; a failure leaves the grant and says why (`trust_not_set`). A closed repository root is never trusted.
- Why: Claude Code refuses a background session in a folder it never trusted and asks only in a terminal, so a folder opened from afar still couldn't take a session.
- Expect pushback: the bridge writing another program's config file, which Claude Code also rewrites while it runs (a lost update is possible, though rare). Claude Code's own safety check refused writing this change until the owner approved it directly. Depends on 6o. Test first: `tests/test_access.py`.

### 6p. The delegation rules in the manual — fork `26651d6`

- What: the manual says to resume with `attach_conversation`, to give a session a self-contained brief and ask it to propose changes before applying them unless the owner said otherwise, and to confirm work done as agreed in a few words. Moved from the owner's own assistant memory. Fork-specific wording; offer only as part of 6e.

### 6q. Opus when no model is asked for — fork `f8f6f02`

- What: `start_active_session` asks for `opus` when no `model` is given (`DEFAULT_MODEL`); a named model still wins. Test: `tests/test_start.py`.
- Why: Claude Code's own default follows the account (Fable on the owner's), while the owner wants Opus for everyday work.
- Expect pushback: a hard-coded model; upstream would more likely take it as a setting (an env variable, unset by default).

### 6r. "Remote Control" instead of "background" in the session tools — fork `db26ff1`

- What: titles and descriptions of the start, resume and stop tools say the sessions are on Remote Control, not "background"; kept by `tests/test_surface.py`.
- Why: agents told the owner sessions ran "in the background", when they are visible on the phone and in the desktop sidebar.
- Upstream relevance: low; this fork's wording, and upstream's sessions may not be on Remote Control.

### 6s. Waking the sessions a shutdown interrupted — fork `ffdd67f`, fixed in `07b58bd`

- What: when the served bridge starts, it wakes in place (`claude --resume <id> --bg`, saved options and Remote Control kept) each background session it started or resumed that was running at its last look (the watcher's `live_snapshot`, kept in the database) and that `claude agents --json --all` now lists as failed. Each wake or failure is logged. `ffdd67f` used the machine's boot time instead; on Windows with Fast Startup that stays weeks old, so `07b58bd` replaced it.
- Why: a shutdown stops every background session and nothing restarts them; the owner could only get one back by asking an agent. The bridge starts at logon, so it can.
- Expect pushback: it starts processes nobody asked for at that moment; upstream may want it behind a setting. Relies on Claude Code marking an interrupted session failed (documented in `agent-view`). Test first: `tests/test_wake.py`.

### 6t. Renaming a session the bridge started — fork `7b97838`, `8322c03`, `639d09d`

- What: `rename_session(session, name)` writes the new name into Claude Code's job record for that background session (`~/.claude/jobs/<id>/state.json`: `name`, `nameSource: "user"`, and the `--name` in `respawnFlags`); a running session watches that record and renames itself within seconds, and a wake keeps it. Sessions the bridge started only (`# RULE`), never mid-turn. The first two commits wrote to the conversation instead, which didn't reach the session lists; fold all three into one pull request as the last one's shape.
- Why: the assistant names a session at start and could never change its mind.
- Expect pushback: it writes Claude Code's internal state, an undocumented format found by reading Claude Code's code (`jobStateNameSync`), which may change with any release. Tests: `tests/test_rename.py`.

### 6u. A news line on every result, replacing whats_new — fork `68b1a07`, `5ae1dd2`

- What: each tool result for an MCP session can start with `news`, one line naming the sessions that conversation started, resumed, messaged or read that have turns past its read marker (a relayed message never counts; turns the owner typed are counted as such), with their state (finished, still working, waiting, ended), and any session waiting on a permission prompt. Markers live in memory, per `mcp-session-id`. `whats_new` and its cursor are removed (`5ae1dd2`); the watcher stays for the wake at start (6s).
- Why: a polling tool nobody called; the relay kept missing replies, most of all when the owner wrote to a session directly.
- Expect pushback: an extra field on every result, and a removed tool; depends on 6n's per-conversation identity and on transcript `origin` entries. Test first: `tests/test_news.py`, over HTTP.

### 6v. A finished turn's last message whole — fork `5de28db`

- What: once a turn has ended, `wait_for_session`, `ask_active_session` and `read_session_output` return its last message whole up to `FINAL_CHARS` (12,000), with `reply_cut`/`reply_length` (or `cut`/`length`) past that; other turns keep `max_chars`.
- Why: final reports of 5,000 to 6,000 characters needed two or three more reads. Small and independent; offer early. Test first: `tests/test_final_reply.py`.

### 6w. Briefs written by the session that holds the context — fork `9fdd4ba`

- What: one line in the manual's relay scope. Fork-specific wording; only with 6e.

### 6x. Starting sessions in the background on Remote Control — fork `4e09bfe`, `968f85b`, `54250cc`, `f75e928`

- Status: an idea for later (owner, 2026-11-06); nothing prepared, no branch.
- What: a session the bridge starts, or resumes from an earlier conversation, runs as an ordinary Claude Code background session with Remote Control on, with no window; the owner opens it from the phone or the desktop app.
- Why upstream should want it: upstream's own commit `248f405` (2026-11-02) opens a terminal window for the same reason, sessions the phone couldn't reach. The background way needs no desktop, no terminal program and no tmux, and works the same on Windows, macOS and Linux.
- Expect pushback: upstream just built the window, with settings and a companion-app page for it. Offer it as one more launch mode (`launch = "remote_control"` or similar) next to theirs, not as a replacement. Depends on how upstream's start is shaped after the resync; rebuild it on their launch settings.
- Test first: the fork's start tests (`tests/test_start.py`), rewritten against upstream's launch settings.

### 6g. Handing a session to the desktop app — fork `d5cb737`

- What: `hand_to_desktop(name)` stops a session `start_active_session` started and opens a named Windows Terminal tab running `claude --desktop --resume '<id>'`, which brings it into the Claude desktop app's sidebar. Refuses sessions the bridge didn't start, mid-turn ones and ones with no conversation yet. A Remote Control hook (`REMOTE_CONTROL_AT_START`, off) for `claude --bg --remote-control`.
- Why: sessions the bridge starts are otherwise invisible in the desktop app.
- Upstream relevance: medium; Windows-only as written (Windows Terminal, PowerShell), macOS would need its own terminal. Not tried live yet. Tests: `tests/test_handover.py`.

### 6e. A manual for the assistant — fork `6c22d09`, cut down in `443c172`

- What: the server instructions rewritten as a short manual (roles, the rule that the owner sees nothing passing through tools, keeping agents' words apart, the relay's scope, how to wait, what only the owner answers), and a `guide` tool returning the same text for clients that drop server instructions. Since `443c172` it holds only what spans tools; what one tool needs is in that tool's description (6h).
- Expect discussion: the manual is written around this fork's voice relay naming; upstream would take the structure and the guide tool, and keep its own words. Test: `tests/test_guide.py`.

### 6f. "List every session" instruction and the relay label — fork `b3f5634`

- What: one paragraph telling the assistant to name every session from every source when asked. Folded into 6e's manual; only worth offering as part of it.

### 7. Serving under a path prefix — not in the fork yet

- What: the bridge works behind a reverse proxy under a path (`CLAUDE_VOICE_PUBLIC_URL=https://host/claude-voice`) only because Nginx also forwards three root paths. One of them is the bridge's own doing: the consent form posts to an absolute `/oauth/consent` (`src/claude_voice/oauth.py`, `_consent_form`). A relative action (`action='consent'`) would remove it. The other two are where OAuth clients look for metadata (RFC 9728, RFC 8414) and can't be avoided by the bridge alone; document them.
- Why: upstream documents Tailscale Funnel only; a prefix behind an existing reverse proxy is a common setup.
- Would need: the change, a test that the form's action stays under the public URL, and a README section on running behind a reverse proxy.

### 8. Windows in the README and CI — prerequisite for 1–3

- Upstream says "Linux or macOS". Supporting Windows means a decision record (scope: the bridge runs on Windows; the systemd service and `install.sh` stay Linux-only; Windows runs it by hand or as a startup task), a README section, and a Windows job in `.github/workflows/checks.yml` so it stays working (today every job is `ubuntu-latest`).
- Without the CI job, Windows support would rot; upstream's own rule ("every rule is enforced by something that can fail") argues for it.
- Best offered first as an issue, asking whether Windows is wanted at all, before any pull request.

### 0. A test that read the shell's environment — fork `d91a67a`

- What: `test_the_configured_courier_name_is_ignored_even_after_the_courier_is_gone` clears `CLAUDE_VOICE_COURIER_NAME` first; the hook reads the environment before the settings file, so the test failed in any shell where the bridge's variable was set. Trivial, independent, offer first.

## Not for upstream

- The owner's setup around the fork (Windows service scripts, the server proxy, its values and working notes), kept in a private repository: specific to that setup.
- The fork's `CLAUDE.md` (fork `27a1b0f`): each session fetches upstream and offers the owner a resync when the fork is behind, the last sync being the merge base of `main` and `upstream/main`. Upstream has its own `CLAUDE.md` since; merge the two at the resync.
- The commit-msg hook calls `python3`, which the python.org installer doesn't create on Windows (only `python.exe`), so the hook fails there unless something else provides `python3`. Fixed on this PC outside the repo, with a `python3.exe` link. Worth a one-line issue at most, not a fork change.

## Still open in the fork

- The session tree (`tree.py`, `SOCKET_PID`) links sessions that message each other through a process id read from Unix socket names. Windows uses named pipes (`\\.\pipe\LOCAL\cc-msg-…`, no pid). Name matching already works; the fallback needs the real Windows address format, now observable since messaging works. Would join candidate 8.
