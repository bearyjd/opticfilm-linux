---
title: "Resume #70 slide PRs: drafts fixed, gate failed only on /tmp quota"
author: Bearyjd
date: 2026-11-07
branch: main
cost: { minutes: 4, tokens: 5676 }
touches:
  - ".omc/STATE.md"
---

## Decided
Resumed from .omc/STATE.md. Fixed the stale "hardware run pending / placeholder" lines in .omc/upstream-pr-drafts/slide-pr-{cli,ir-crop,positive}.md. The b3 worktree is .local-research/publish/upstream-pr-slides-b3.
## Tried and ruled out
First release_check on b3 reported 12 failed suites, but every failure was OSError Errno 122 (disk quota on /tmp): not a code result. Reran after space was freed.
## Watch out
When the /tmp tmpfs quota fills, every Bash call fails and gate runs show spurious FAILs: grep the log for "Errno 122" before believing a red. Don't use pgrep -f in Bash (a hook rejects it).
