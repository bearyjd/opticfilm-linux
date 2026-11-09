---
title: "Resuming with no session file: use STATE.md plus the upstream issue thread"
author: Bearyjd
date: 2026-11-09
branch: main
cost: { minutes: 1, tokens: 11378 }
touches:
  - ".omc/STATE.md"
---

## Decided
When /resume-session finds no files in ~/.claude/session-data/, resume from .omc/STATE.md (<=60 lines) and, for "cgillinger wrote back", read the latest comments on cgillinger/opticfilm135i-linux#70 (`gh issue view 70 -R cgillinger/opticfilm135i-linux --comments | tail -60`). Report only; his updates are status, not requests, and nothing is posted upstream without the owner's go.

## Tried and ruled out
`gh search issues --author cgillinger` only lists issues he opened, not replies in existing threads. Use gh issue view on the known thread instead.

## Watch out
As of 2026-11-09 his v10 backend `holder` option is offline-only on his branch; the hardware run on the owner's unit waits until he names a build after his own empty-holder run.

## Addendum (same session)
- Decided: driver-latest took upstream #83 by cherry-picking its two commits (-x) as fork PR #30 (merged 76d2a85); fork #29 closed, overlap thread deferred. Owner still owes one hardware `--slides` SHA-256 comparison and a 7200 dpi peak-memory check.
- Tried and ruled out: merging upstream master into the fork. It conflicts in cli/device/holder/recrop, so take narrow commits instead.
- Heirloom #146 (Renovate Python 3.15) closed; hold rule is heirloom #147.
