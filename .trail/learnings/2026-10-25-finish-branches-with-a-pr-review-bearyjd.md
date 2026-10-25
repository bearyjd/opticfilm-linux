---
title: "Finish branches with a PR, review it, merge on green CI"
author: Bearyjd
date: 2026-10-25
standing: true
---

## Decided
- Finished work goes on a branch and is pushed as a PR. Do not merge locally to main. The user said "always push to a pr".
- Review PRs adversarially with Codex (`/codex`) and fix every finding.
- Merge once CI is green. The user allowed this for the PRs in the overnight run. Use a regular merge commit, not squash, so the stacked PRs stay clean.

## Watch out
- The user has also said merging and deleting branches stay with them unless they say otherwise. Merge only when told to, or when this standing instruction applies.

_Told to Claude in 2 past sessions here, most recently on Oct 22._
