# claude-voice

How a change is made here is in [CONTRIBUTING.md](CONTRIBUTING.md). What an
assistant must and must not do, installing the bridge or changing it, is in
[AGENTS.md](AGENTS.md). Both apply to every Claude session in this repository.

## The owner's fork

- This fork is AI-managed by its owner, kelno, under kelno's own rules, which don't apply to anyone else's agents; for anyone else, `CONTRIBUTING.md` is how a change is made.
- For kelno's agents working on the fork itself, the gates in `CONTRIBUTING.md` are slow checks, run as kelno's rules for AI-managed repos say, not before every push. The quality ratchet isn't run for work on the fork's own `main`: it compares a change with `main`, which matters only for a pull request, and measuring `main` in a copy runs the suite twice. [P107] Here the suite runs on 8 parallel workers: `uv run --with pytest-xdist pytest -q -n 8 --cov` in place of the gates' `uv run pytest -q --cov` (about 50 s instead of about 5 minutes); `pyproject.toml` and `uv.lock` stay as upstream has them. [P109] Nothing is recorded in `quality.json`: since upstream's ADR-CV-009 (2026-11-10), continuous integration keeps main's levels with each of its runs. A branch meant for a pull request to upstream is checked as `UPSTREAM.md` says ("How we check a pull request here").
- This fork follows upstream under kelno's rules for forks, kept in kelno's private agent setup; they include an upstream check at the start of a session, how a resync is done, and recording each difference. What is specific here:
  * The record of how the fork differs is `UPSTREAM.md`, "Differences from upstream", naming fork commits by their subject; each upstream feature's on or off is under "Upstream features, and how they're set here".
  * An upstream tool the owner hasn't decided on is switched off by naming it in `SWITCHED_OFF` in `server.py`: not registered, its code kept, upstream's tests of it run with it switched back on. An upstream default voice rule is on only when named in `VOICE_RULES_ON`. [P72]
  * The fork's decision records are numbered from 101 (`decisions/README.md`).
  * `UPSTREAM-CANDIDATES.md`, what could go back upstream, is kept current whenever the fork gains, changes or drops a commit, or upstream takes one: each candidate names its fork commits, why upstream would want it, the pushback to expect, and how upstream's test-first rule applies. A candidate upstream has taken leaves the candidates; a merged pull request of ours gets a line under "Merged upstream" at the end, its link and two or three sentences. Like `UPSTREAM.md`, it describes what is true now.
