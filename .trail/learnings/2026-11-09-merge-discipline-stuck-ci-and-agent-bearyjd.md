---
title: "Merge discipline, stuck CI, and agent-worktree pitfalls (2026-11-08/09)"
author: Bearyjd
date: 2026-11-09
branch: docs/trail-learnings-20261109
cost: { minutes: 26, tokens: 404401 }
touches:
  - src/opticfilm_linux/batch/guess.py
  - tests/test_batch_guess.py
  - src/opticfilm_linux/app/qml/StartPage.qml
  - tests/qt/test_start_page_guess.py
  - tests/test_app_auto_advisory_flags.py
  - ".omc/STATE.md"
---

## Decided
- Merge only after the checks have FINISHED green. `gh pr merge --auto` merges immediately on a repo with no required checks (heirloom #135 went in before its CI had reported; it passed afterwards). Wait with `gh pr checks N --watch`, then merge. A watcher script must first wait until checks exist (`gh pr checks` says "no checks reported" right after a push or rebase) and then require every line to be pass or skipping.
- When GitHub's hosted runners leave a PR's jobs queued for hours with no incident on githubstatus and no concurrency group: cancel the queued runs, then `gh pr close N && gh pr reopen N`. That fires fresh pull_request runs without adding commits.
- Upstream PR conflicts: if the maintainer asks for a rebase but the branch is a PR branch, merge the base into it (no force-push) and say so in the PR comment. Show the comment text to the owner before posting.
- To carry one commit out of a stale PR branch, cherry-pick it onto current master and open a new PR; do not merge the old branch (its history can hold commits you must not publish, e.g. a reintroduced real email in HANDOFF.md).
- Agent worktrees (`isolation: "worktree"`) are created from the session's primary working directory. Reset it to the target repo (a Bash `cd`) before spawning, and tell each agent to verify `git remote -v` first.

## Tried and ruled out
- `git checkout --theirs <file>` to resolve a cherry-pick conflict on a file master has since changed: it takes the whole old file and silently drops master's later changes (72 lines of heirloom `_shared.py`). Lint exposed it. Restore master's file and apply only the hunk by hand.
- Blaming the merge for a failing `test_postscan_perf`: the same commit failed in the old worktree and passed in a fresh checkout. Cause was stale `__pycache__` with identical source/pyc timestamps. `PYTHONPYCACHEPREFIX=<scratch>` runs against a clean bytecode cache without deleting anything.

## Watch out
- Renovate PRs that touch the same workflow files conflict after the first one merges: tick the "rebase/retry" checkbox in the PR body and wait for the new head before merging the second.
- `git worktree prune` drops entries whose directories a reboot wiped (/tmp scratchpad worktrees): check `git branch --no-merged main` first so no unmerged branch is forgotten.
- A background watcher dies with a reboot; restart it and re-check the PR state instead of assuming it merged.
