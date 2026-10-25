---
title: Setting up trail and pushing from this machine
author: Bearyjd
date: 2026-10-25
branch: chore/add-trail
cost: { minutes: 5, tokens: 53500 }
---

## Decided
- Commit only `.claude/settings.json` and `.trail/` for trail setup. Leave `.claude/settings.local.json` and `.claude/worktrees/` out; they are local-only.
- Trail setup goes on its own branch and PR, never straight to main.

## Tried and ruled out
- A plain `git push` fails here: the global credential helper points at `/home/linuxbrew/.linuxbrew/bin/gh`, which does not exist.

## Watch out
- Push with `git -c credential.helper= -c "credential.helper=!/usr/bin/gh auth git-credential" push ...`, and only after the owner says to push.
