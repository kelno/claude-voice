# claude-voice, the owner's fork

- At the start of a session, run `git fetch upstream`. If `git rev-list --count $(git merge-base main upstream/main)..upstream/main` is above 0, tell the owner how many upstream commits the fork is behind and offer a resync, but never start one without the owner's OK. The last sync is the merge base of `main` and `upstream/main`, so it needs no marker of its own; a resync that rebuilds `main` on top of upstream moves it.
