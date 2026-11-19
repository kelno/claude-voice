# claude-voice

How a change is made here is in [CONTRIBUTING.md](CONTRIBUTING.md). Everything else an agent follows in this fork is its owner's: the section below, and kelno's own rules.

## The owner's fork

- This fork is AI-managed by its owner, kelno, under kelno's own rules, which don't apply to anyone else's agents; for anyone else, `CONTRIBUTING.md` is how a change is made.
- When working on the fork itself as one of kelno's agents, follow `CONTRIBUTING.md`; its gates are slow checks, run as kelno's rules for AI-managed repos say, not before every push. The quality ratchet isn't run for work on the fork's own `main`: it compares a change with `main`, which matters only for a pull request, and measuring `main` in a copy runs the suite twice. [P107] Here the suite runs on 8 parallel workers: `uv run --with pytest-xdist pytest -q -n 8 --cov` in place of the gates' `uv run pytest -q --cov` (about 50 s instead of about 5 minutes); `pyproject.toml` and `uv.lock` stay as upstream has them. [P109] Nothing is recorded in `quality.json`: since upstream's ADR-CV-009 (2026-11-10), continuous integration keeps main's levels with each of its runs. Before a pull request to upstream, check its branch as `UPSTREAM.md` says ("How we check a pull request here").
- When you change what the bridge says to an assistant (the guide, tool descriptions, what results say), write and check it as `CLIENT-TEXT.md` says.
- This fork follows upstream. What is specific here:
  * When the fork gains or loses a difference from upstream, record it in `UPSTREAM.md`, "Differences from upstream", naming fork commits by their subject; each upstream feature's on or off is under "Upstream features, and how they're set here".
  * An upstream tool the owner hasn't decided on is switched off by naming it in `SWITCHED_OFF` in `server.py`: not registered, its code kept, upstream's tests of it run with it switched back on. An upstream default voice rule is on only when named in `VOICE_RULES_ON`. [P72]
  * The fork's decision records are numbered from 101 (`decisions/README.md`).
  * The bridge handles every session, not only those it started (ADR-CV-108): at each resync, anything upstream adds or changes that acts only on a session the bridge started is widened to every session it can act on, and recorded in `UPSTREAM.md`. What a session is (in a window, in the desktop app, in a folder not open to the bridge) may still limit it; who started it doesn't.
  * Put each of the fork's own features in a file of its own, never inside upstream's files; upstream's code only hooks it in. Upstream changes its main files often, and every fork line among theirs is a conflict waiting at the next merge. [P120]
  * When the fork's commits change or upstream takes one of them, bring `UPSTREAM-CANDIDATES.md`, what could go back upstream, up to date: each candidate names its fork commits, why upstream would want it, the pushback to expect, and how upstream's test-first rule applies. A candidate upstream has taken leaves the candidates; a merged pull request of ours gets a line under "Merged upstream" at the end, its link and two or three sentences. Like `UPSTREAM.md`, it describes what is true now.
