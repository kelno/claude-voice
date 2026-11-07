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

If you are a session working on this repository, rather than installing it:

- Follow [CONTRIBUTING.md](CONTRIBUTING.md): the gates, a failing test before
  the code, the mutation run, and the three rules for every change.
- Keep the quality ratchet: run `uv run python scripts/ratchet.py update` before
  the last push and commit `quality.json`. Never make a measure worse without a
  `Ratchet-Exception:` line in the pull request saying why.
- Push only to your owner's fork; a pull request goes to
  `roykollensvendsen/claude-voice` with `gh pr create --repo`.
- Never rename or remove a tool or field that Hermes or the voice app uses.
- Never put a `Claude-Session:` line or a claude.ai/code session address in a
  commit message; the checks refuse it. Credit with `Co-Authored-By:` only.

## Writing what the client reads

Everything the bridge says to an assistant is text the assistant has to understand without seeing the code: the manual (the server's instructions, also returned by the `guide` tool), each tool's title and description, the names and descriptions of its parameters, and what its results say (field names, status values, notes, the news line, error messages). Together they are the bridge's interface: an API whose contract is written in prose. Treat it as you would a codebase's public API, with the same habits; the rules below are those habits applied to text. Keep every rule about writing it in this section.

### The text is an interface contract

- The contract: what the text promises is what the assistant relies on, as a caller relies on a function's signature and docstring. If a description says a tool returns "still_working", the tool returns exactly that; if the manual says a reply is final only once the session's turn has ended, every tool reports it the same way.
- Single source of truth (say it once): each rule lives in one place, where the assistant will be when it needs it, as a constant is defined once and imported everywhere else. A tool's description holds what concerns that tool. The manual holds only what spans tools (the roles, the central rules) or doesn't fit in one description. A rule copied into several descriptions drifts, like duplicated code.
- No defensive copies: the manual is assumed read, as a module relies on its documented preconditions. Descriptions and results don't repeat its rules as reminders. An assistant that doesn't read it is a bug to fix in the manual (its first line, a clearer rule), not a case to design around.
- Consistent naming: treat the words that name things (statuses, fields, tool names, a term the manual defines) as identifiers. One name per concept, the same name everywhere, never a synonym for variety. Renaming one is a rename across the whole surface, not a local edit.
- A change ripples to every caller: before changing one piece, find everything that refers to it, as you would before changing a signature: the manual's rules, other descriptions, result notes, and the tests that pin exact phrases (`tests/test_guide.py`, `tests/test_surface.py`).

### What the assistant sees, and when

- The short listing: before a tool is loaded, the Claude app lists it with only the start of its description, normally about 80 characters. That is all the assistant has to decide whether to load the tool, so the first line's one duty is to give what is needed to pick it; `tests/test_surface.py` keeps it one sentence of at most 78 characters.
- In some cases the app inserts text before the description, inside those 80 characters. A real example, from a claude.ai voice chat in November 2026: the listing began with `[Custom connector "Claude Voice", added by the user] `, leaving about 27 characters of the description, so `ask_active_session` showed as "Ask a session and wait (8 …" (its wording at the time, since changed). The prefix holds the connector's name, so its length varies by connector. It may be a bug in the app, and is still being looked into; earlier the same month the listing showed about 79 characters of the description with no prefix. To compensate, put what matters most in the first few words: what the tool is for comes before any detail.
- Loading: the assistant searches for tools by what it needs, and the search returns several likely tools' full descriptions and parameters. That result is inserted into the conversation at the moment of the search, like any tool result; it is not a fixed part of the context, as the system prompt is. Once loaded, the whole description is there, so how to use the tool can go in later lines when it doesn't fit in the first.
- Because a loaded description is a search result in the history, not a live view of the tool, it can be out of date (the tool changed since the search: a bridge restart with new wording, a new parameter), it can disappear (compacting a long conversation can summarise it away), and after a reconnect (seen when a voice call rejoined its chat) the tool must be searched for again before it can be called: the old result may still be in the history, but the tool is not loaded. So the assistant falls back to the short listing mid-conversation, not only at first contact, and the first line has to do its job every time.
- The server's instructions have been seen not to reach the assistant at all in the Claude app. That is why `guide` returns the same text and its first line says to read it first, and why the first result of an MCP session that hasn't called `guide` carries the manual.

### Checking new wording

- A cold reader is an assistant that reads the text with only what a client would have: not the code, not the discussion that led to the wording, not what it was meant to say. A cold read is what it reports back: what it understands the text to say, and what it would do.
- By default, when an assistant the text applies to (one that uses the bridge) is in the conversation, ask it how it reads a change before committing, when that's possible. It acts as a cold reader: its reading shapes the wording and is never an approval, which stays with the person you work for.
- A big change (a new or reworked tool, a reworked rule in the manual, several descriptions at once) ends with a cold read by fresh assistants given only what a client sees, or a session that uses the tools on real cases. Fix what they misread or trip on before calling the change done. Small wording changes don't need one.
