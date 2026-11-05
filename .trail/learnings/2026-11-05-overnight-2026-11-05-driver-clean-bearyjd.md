---
title: "Overnight 2026-11-05: driver clean caused blotches and slow trays; CI lessons for agent-built PRs"
author: Bearyjd
date: 2026-11-05
branch: docs/learning-resume-2026-11-04
cost: { minutes: 509, tokens: 892908 }
touches:
  - ".omc/upstream-pr-drafts/issue35-ab-gate-results-2026-11-04.md"
  - scripts/hardware/merge_when_green.sh
  - ".omc/STATE.md"
---

## Decided
- The 3600 dpi "blocky blotches" and the 2.5 min/frame post-scan wait were the DRIVER's own IR clean (the app never passed --no-clean). #95 makes --no-clean the default (OPTICFILM_DRIVER_CLEAN=1 restores). The 175 s/frame scan pass is hardware; do not try to speed it.
- Rotate lag was a full 7200 dpi re-enhance per rotate; #99 turns the stored preview from a lossless master instead.
- Every agent-built PR goes: builder (tests first) -> I verify -> independent Opus/Sonnet review -> fixes -> merge main -> whole-repo ruff check + whole non-Qt suite + Qt -> push -> scripts/hardware/merge_when_green.sh. State lives in .omc/STATE.md.
## Tried and ruled out
- Trusting a builder's own "all green": CI failed repeatedly on repo-wide source-rule tests (no changed function >= 50 lines, line limit 100), on a semantic clash after merging main (slide.index+1 vs holder position, dict vs list) and on timing races in tests.
- git stash / checkout in shared worktrees: agents did it; the stash stack is shared; keep agents out of .local-research/publish/driver-latest (the production + watcher checkout).
## Watch out
- merge_when_green.sh can say "Cannot update PR branch due to conflicts": merge origin/main locally, fix, push, re-run it.
- Review caught real bugs tonight (wrong-slide metadata edit when clicking while typing; cleanup stalling after a metadata edit): always review UI/state changes independently and reproduce with real clicks.
