---
title: "Overnight 2026-11-06 resume: queue drain state and flaky-stub reds"
author: Bearyjd
date: 2026-11-07
branch: docs/learning-resume-2026-11-04
cost: { minutes: 1, tokens: 54918 }
touches:
  - ".omc/STATE.md"
  - ".omc/plans/overnight-2026-11-07.md"
  - tests/test_eject_watch_check.py
---

## Decided
Resumed the overnight plan from .omc/STATE.md and .omc/plans/overnight-2026-11-07.md. Step A first: check `gh pr list` and the merge_when_green logs in .local-research/merge-requeue-N.log. At 22:40 #114, #115 and #120 were merged; #116, #117, #118, #119 and #121 plus fork #27 were still draining in detached scripts.
## Tried and ruled out
The reds on #117 and #119 (tests/test_eject_watch_check.py::test_journal_calls_are_bounded_even_when_the_tool_hangs) came from before the #120 stub fix. They are not a new bug. The merge script updates the branch from main, so the rerun should pick up the fix. No manual fix was made.
## Watch out
A foreground wait loop over 600 s gets moved to the background by the harness. CI test runs take 10 to 12 minutes, so wait with a background job instead. B5 core, UI, SANE replay part 2, the upstream draft and housekeeping had not started when this note was written.
