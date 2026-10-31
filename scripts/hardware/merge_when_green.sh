#!/bin/bash
# Optional maintainer tooling (needs gh, flock, python3).
# usage: merge_when_green.sh <owner/repo> <pr> <branch>
# Serialised (flock): update branch from base, wait for CI green on the CURRENT head, merge with a merge commit. Never deletes branches.
REPO=${1:?usage: merge_when_green.sh <owner/repo> <pr> <branch>}
PR=${2:?usage: merge_when_green.sh <owner/repo> <pr> <branch>}
BR=${3:?usage: merge_when_green.sh <owner/repo> <pr> <branch>}
exec 9>"${TMPDIR:-/tmp}/opticfilm-merge-queue.lock"; flock 9
retry() { for _ in 1 2 3 4 5; do out=$("$@" 2>&1) && { echo "$out"; return 0; }; sleep 10; done; echo "$out"; return 1; }
retry gh pr update-branch "$PR" --repo "$REPO" | tail -1; sleep 30
v=none
for _ in $(seq 1 200); do
  head=$(retry gh pr view "$PR" --repo "$REPO" --json headRefOid --jq .headRefOid) || { sleep 60; continue; }
  runs=$(retry gh run list --repo "$REPO" --branch "$BR" --limit 20 --json headSha,status,conclusion) || { sleep 60; continue; }
  v=$(echo "$runs" | python3 -c "
import json,sys
h='$head'; d=[r for r in json.load(sys.stdin) if r['headSha']==h]
print('none' if not d else 'pending' if any(r['status']!='completed' for r in d) else 'green' if all(r['conclusion']=='success' for r in d) else 'red')")
  echo "PR $PR head=${head:0:7} ci=$v"
  [ "$v" = green ] && break
  [ "$v" = red ] && { echo "RED: not merging"; exit 1; }
  sleep 120
done
[ "$v" = green ] || { echo "TIMEOUT: not merging"; exit 2; }
retry gh pr merge "$PR" --repo "$REPO" --merge | tail -1
st=$(retry gh pr view "$PR" --repo "$REPO" --json state --jq .state); echo "PR $PR state=$st"
[ "$st" = MERGED ]
