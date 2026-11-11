# For an assistant setting this up for someone

You are helping a person install claude-voice, a bridge that lets a voice
assistant drive the Claude Code sessions on their computer. The steps
themselves are in the README's "Start here" section; follow them there rather
than from memory, because continuous integration proves that section on an
empty machine and nothing proves this page.

## Let the check lead

`claude-voice check --json` lists every check with `ok`, `required`, `detail`
and `fix`. Run it after each step, do the `fix` of the first required check that
fails, and stop when `ready` is `true`. Add `--transport http` once the person
wants to reach the bridge from a phone.

## What only the person can do

- **Sign in to Claude.** `/login` inside `claude` opens a browser for their own
  account. Ask them to do it; never sign in for them.
- **Approve a public address.** Exposing the bridge to the internet (for
  example `tailscale funnel`) changes their network. Ask first.

## What never to do

- **Never print the login secret** (`CLAUDE_VOICE_TOKEN`, kept in
  `~/.config/claude-voice/env`). It signs in anyone who has it. If the person
  needs it on another device, put it on their clipboard or tell them where the
  file is.
- **Never set `ANTHROPIC_API_KEY`** to get past a sign-in problem. The bridge
  refuses it on purpose: the person's subscription pays, not per-token billing.
  `CLAUDE_VOICE_ALLOW_API_KEY=1` is for a person who has chosen that, not for
  you.

## When the person wants another machine reached

The steps are in [docs/other-machines.md](docs/other-machines.md). On the other
machine, `claude-voice agent install --sessions` checks what is missing and says
what to add on the hub. Then:

- **Pairing is the person's.** They type the code the machine shows, and then its
  name, in `claude-voice machines approve <name>` on the hub. Never type either
  for them, and never pipe the code into it. A changed tool list makes the hub
  refuse the machine until they approve it again; that is the check working, not
  a fault to work around.
- **Never widen what an agent offers** to get something done: it offers its
  session tools and its own approved plugins, nothing about the bridge.
- **Never copy a key between machines.** The hub's key for a machine is in
  `~/.config/claude-voice/machines/<name>/key`; do not print it, move it or put it
  in a message.
- **Ask before installing** the agent on a machine, before `sudo` of any kind
  there, and before keeping a shell login on it once the agent is set up.

## When something fails

- **`check` is not ready:** do its `fix`.
- **The service will not stay up:** read `journalctl --user -u claude-voice -n 50`.
- **Something went wrong earlier**, such as the phone's connector showing as
  disconnected: `~/.local/state/claude-voice/bridge.log`, next to the bridge's
  database, keeps every request, each token refresh with its outcome, and each
  tool call, across restarts.
- **A phone cannot connect:**
  - `curl https://<their host>/healthz` should print `ok` from outside;
  - port 443 is the one claude.ai reaches.
- **The bridge runs but a tool misbehaves:**
  - its `health` tool lists the latest errors;
  - each error carries the trace id of the call that caused it.

## When you are changing claude-voice itself

If you are the session working in this repository on the owner's own machine,
you are the manager for the bridge and every plugin: see
[docs/organisation.md](docs/organisation.md) and the `manage` skill.

If you are a session working on this repository, rather than installing it:

- Follow [CONTRIBUTING.md](CONTRIBUTING.md): the gates, a failing test before
  the code, the mutation run, and the three rules for every change.
- In a fresh clone, give the repositories their permission rules with
  `uv run claude-voice permissions`, after a `--dry-run` the person has seen. It
  only adds rules, keeps theirs and backs the file up once
  ([docs/permissions.md](docs/permissions.md)). Never edit their settings by hand
  to get past a refused command.
- Keep the quality ratchet: `uv run python scripts/ratchet.py check --base origin/main`
  must hold. Never make a measure worse without a `Ratchet-Exception:` line in
  the pull request saying why. Never edit `quality.json` to pass: main keeps the
  levels, and the file holds only the time budget.
- Push only to your owner's fork; a pull request goes to
  `roykollensvendsen/claude-voice` with `gh pr create --repo`.
- Never rename or remove a tool or field that Hermes or the voice app uses.
- When an outside contributor's pull request lands, thank them in it and say
  concretely what was good. Hold your own pull requests to what contributions
  have taught (CONTRIBUTING, "When someone else's change comes in").
- Never put a `Claude-Session:` line or a claude.ai/code session address in a
  commit message; the checks refuse it. Credit with `Co-Authored-By:` only.

## Writing what the client reads

Everything the bridge says to an assistant is text the assistant has to understand without seeing the code: the manual (the server's instructions, also returned by the `guide` tool), each tool's title and description, the names and descriptions of its parameters, and what its results say (field names, status values, notes, the news line, error messages). Together they are the bridge's interface: an API whose contract is written in prose. Treat it as you would a codebase's public API, with the same habits; the rules below are those habits applied to text. Keep every rule about writing it in this section.

### Who reads what

- The relay, any client that calls the bridge's tools to work with sessions (a claude.ai chat, voice or text, or a Claude Code session with the connector, including a desk session that starts or messages other sessions), reads the guide and the tool descriptions. The guide is written to it.
- A session receiving relayed messages reads the note attached to each one; that note carries everything it needs to answer.
- A session that calls one of the bridge's tools for itself (such as `confirm_ask`) reads that tool's description, which stands alone for a reader who has never seen the guide.
- A side session reads its question and the prompt the bridge gives it, nothing else.
- Every client is handed the guide the same way, whatever it is. What keeps a session that relays nothing from following it is the guide's own opening, which says who it is for: it is unmistakable from the first lines that the guide is for the relay.

### The text is an interface contract

- The contract: what the text promises is what the assistant relies on, as a caller relies on a function's signature and docstring. If a description says a tool returns "still_working", the tool returns exactly that; if the manual says a reply is final only once the session's turn has ended, every tool reports it the same way.
- Single source of truth (say it once): each rule lives in one place, where the assistant will be when it needs it, as a constant is defined once and imported everywhere else. A tool's description holds what concerns that tool. The manual holds only what spans tools (the roles, the central rules) or doesn't fit in one description. A rule copied into several descriptions drifts, like duplicated code.
- Avoid defensive copies: the manual is assumed read, as a module relies on its documented preconditions, so descriptions and results don't normally repeat its rules as reminders: a copy drifts, and lengthens what the assistant reads. An assistant that doesn't follow a rule is first a reason to fix the manual (its first line, a clearer rule). A reminder in a description or a result is allowed where a rule slipped despite the manual, such as one that fights a strong model default or an instruction the app inserts at that moment; a code comment next to it says why it's needed there. The owner's choice, 2026-11-11: the rule used to forbid any reminder.
- Consistent naming: treat the words that name things (statuses, fields, tool names, a term the manual defines) as identifiers. One name per concept, the same name everywhere, never a synonym for variety. Renaming one is a rename across the whole surface, not a local edit.
- A change ripples to every caller, both ways: before changing one piece, find everything that refers to it, as you would before changing a signature: the manual's rules, other descriptions, result notes, and the tests that pin exact phrases (`tests/test_guide.py`, `tests/test_surface.py`).

### What the assistant sees, and when

- The short listing: before a tool is loaded, the Claude app lists it with only the start of its description, normally about 80 characters. That is all the assistant has to decide whether to load the tool, so the first line's one duty is to give what is needed to pick it; `tests/test_surface.py` keeps it one sentence of at most 78 characters.
- In some cases the app inserts text before the description, inside those 80 characters. A real example, from a claude.ai voice chat in November 2026: the listing began with `[Custom connector "Claude Voice", added by the user] `, leaving about 27 characters of the description, so `ask_active_session` showed as "Ask a session and wait (8 …" (its wording at the time, since changed). The prefix holds the connector's name, so its length varies by connector. It may be a bug in the app, and is still being looked into; earlier the same month the listing showed about 79 characters of the description with no prefix. To compensate, put what matters most in the first few words: what the tool is for comes before any detail.
- Loading: the assistant searches for tools by what it needs, and the search returns several likely tools' full descriptions and parameters. That result is inserted into the conversation at the moment of the search, like any tool result; it is not a fixed part of the context, as the system prompt is. Once loaded, the whole description is there, so how to use the tool can go in later lines when it doesn't fit in the first.
- Parameters and results: with the description, the assistant gets each parameter's own description, next to the parameter, in Claude Code and in the Claude app alike; it never gets a tool's result schema (`outputSchema`), even when the tool declares one (both tested in November 2026). So what concerns one parameter goes in that parameter's description (`Annotated[..., Field(description=...)]`), and a parameter several tools share is described once, in one of the `*_PARAM` constants in `server.py`. What a result's fields mean, and what to do with them, goes in the tool's description, the only place the assistant reads it before a call. Tool search's result also shows each parameter in a one-line summary, its description cut after about 115 characters (the full text comes with it): as with a tool's first line, a parameter description's first sentence says what matters most within that, and `tests/test_surface.py` keeps it there.
- Because a loaded description is a search result in the history, not a live view of the tool, it can be out of date (the tool changed since the search: a bridge restart with new wording, a new parameter), it can disappear (compacting a long conversation can summarise it away), and after a reconnect (seen when a voice call rejoined its chat) the tool must be searched for again before it can be called: the old result may still be in the history, but the tool is not loaded. So the assistant falls back to the short listing mid-conversation, not only at first contact, and the first line has to do its job every time.
- The server's instructions have been seen not to reach the assistant at all in the Claude app, and Claude Code puts them in its system prompt cut at about 4,096 characters. So the bridge hands the guide over itself, with the first result of each conversation that hasn't called `guide`: a Claude app conversation is known by its MCP session (the `mcp-session-id` header), a Claude Code session by finding the call's id (`claudecode/toolUseId`) in the transcript of a session running on this computer.
- Compaction can drop the guide, since it arrives as a tool result. For a Claude Code session the bridge sees the compaction in its transcript and hands the guide over again with the next result. A Claude app conversation gives no such sign, so tell its assistant, where it keeps standing instructions (such as the app's memory), to call `guide` again after every compaction, even when the guide still seems to be there.

### Checking new wording

- A cold reader is an assistant that reads the text with only what a client would have: not the code, not the discussion that led to the wording, not what it was meant to say. A cold read is what it reports back: what it understands the text to say, and what it would do.
- By default, when an assistant the text applies to (one that uses the bridge) is in the conversation, ask it how it reads a change before committing, when that's possible. It acts as a cold reader: its reading shapes the wording and is never an approval, which stays with the person you work for.
- A big change (a new or reworked tool, a reworked rule in the manual, several descriptions at once) ends with a cold read by fresh assistants given only what a client sees, or a session that uses the tools on real cases. Fix what they misread or trip on before calling the change done. Small wording changes don't need one.
