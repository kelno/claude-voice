# claude-voice

How a change is made here is in [CONTRIBUTING.md](CONTRIBUTING.md). What an
assistant must and must not do, installing the bridge or changing it, is in
[AGENTS.md](AGENTS.md). Both apply to every Claude session in this repository.

## The owner's fork

- At the start of a session, run `git fetch upstream`. If `git rev-list --count $(git merge-base main upstream/main)..upstream/main` is above 0, tell the owner how many upstream commits the fork is behind and offer a resync, but never start one without the owner's OK. The last sync is the merge base of `main` and `upstream/main`, so it needs no marker of its own; a resync that rebuilds `main` on top of upstream moves it.
- Whenever a change makes the fork differ from upstream (a rename, a rework, keeping the fork's own version over upstream's, dropping or not taking an upstream change), record it in the same change, in `UPSTREAM.md` of the bridge repository this fork is a submodule of (kelno/claude-voice-bridge), under "Differences from upstream": what differs, why, and for a rename the old upstream name. Reasons get lost otherwise: the rename of `side_question` to `peek_session` was found again only from the owner's memory.
