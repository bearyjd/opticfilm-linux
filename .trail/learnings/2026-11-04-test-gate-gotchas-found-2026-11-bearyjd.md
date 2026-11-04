---
title: "Test/gate gotchas found 2026-11-03: nohup ignores SIGHUP, Qt _wait flake, /tmp quota, noreply email"
author: Bearyjd
date: 2026-11-04
branch: docs/ux-design-and-plan
cost: { minutes: 1871, tokens: 1739795 }
touches:
  - scripts/hardware/ab_gate_test_upstream.sh
  - ".omc/plans/overnight-2026-11-03.md"
  - scripts/eject-watch-check.sh
  - tests/test_eject_watch_check.py
  - tests/qt/test_qt_smoke.py
---

## Decided
- The remaining flake in `test_cleanup_signal_cannot_skip_retry_after_failed_stop[1]` was a test-rig defect, not a race in `scripts/eject-watch-check.sh`: when pytest is launched with a signal ignored (`nohup` ignores SIGHUP, background shells ignore SIGINT) the child inherits SIG_IGN across exec and bash cannot trap it. Fixed with an autouse fixture in tests/test_eject_watch_check.py (PR #85). Launch gate scripts with `setsid`, not `nohup`.
- tests/test_review_function_lengths.py rejects ANY edit to a function that is already over 50 lines (e.g. the `rig` fixture, 77 lines): add helpers or fixtures instead of editing it.
- Commits pushed to GitHub must use the repo's noreply identity; passing `-c user.email=<real address>` gets the push declined (email privacy). The local SANE scratch commits were re-authored and squashed before anything is pushed.
- A Fedora CI bump (#63, fedora:46) pulls rawhide packages; its `qt` failure is the same flaky `_wait` timeout that also hits main on fc43. Do not merge Renovate bumps to unreleased Fedora.

## Tried and ruled out
- Raising timeouts or retrying to fix signal/Qt flakes: the signal one was inherited-ignored dispositions, nothing to do with timing.
- Treating `qt` job failures with "timed out waiting for the window" as window/platform problems: that message is hard-coded in `_wait` (tests/qt/test_qt_smoke.py:51) and fits any hung condition.

## Watch out
- `/tmp` is a RAM-backed volume with a quota: screenshot matrices and test batches (4+ GB) killed a Codex run and then every shell command ("Disk quota used up"). Keep captures small and delete named scratch outputs; remove merged worktrees with `git worktree remove` (it refuses if dirty).
- A subagent that runs for an hour producing nothing should be stopped and the job handed to Codex with tighter instructions.
- merge_when_green.sh gets RED if CI fails on the updated branch: rerun failed jobs (`gh run rerun <id> --failed`) only for confirmed flakes, then restart the script.
