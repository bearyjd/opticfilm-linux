---
title: "Resuming after /clear: check STATE.md and PR status first"
author: Bearyjd
date: 2026-10-30
branch: fix/privacy-hygiene
cost: { minutes: 1, tokens: 52287 }
touches:
  - ".omc/STATE.md"
---

## Decided
After a /clear with only "continue", read .omc/STATE.md (<=60 lines) and `gh pr list --head <branch> --state all` before doing anything. On 2026-10-30 the checked-out branch fix/privacy-hygiene was already merged as PR #39 and no app PRs were open, so there was no code work to resume.

## Tried and ruled out
Looking for unfinished code work: none exists. The remaining items need the owner or hardware (A/B gate test, arm-C proof via cal_bufempty_waits, eject-button driver trace).

## Watch out
Do not touch the scanner without the owner's go-ahead. The local branch can still be checked out after its PR has merged, so verify the PR state instead of assuming it is in flight.
