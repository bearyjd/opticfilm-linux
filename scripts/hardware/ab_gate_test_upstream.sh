#!/bin/bash
# Interleaved A/B of the calibration stop gate against UPSTREAM master (cgillinger/opticfilm135i-linux, PR #46).
# Run it YOURSELF in a real terminal, watching the scanner. Same order the upstream maintainer asked for on issue #35:
#   one warm-up scan, then off, on, on, off, off, on at 600 dpi (balanced A B B A A B; A = gate off, B = gate on).
# usage: ab_gate_test_upstream.sh <attempt_dir>
# Env: OPTICFILM_DRIVER_DIR must point at an UPSTREAM master checkout (a read-only `git worktree` of origin/master),
#      NOT at the fork checkout the Eject watcher runs from. PYTHON (default python3) must have pyusb.
#      Run it inside whatever environment has pyusb (the host python; see README.md in this folder).
# Needs: scanner powered on, holder LOADED and latched (run `of135i load` first), idle-homed, watcher unit STOPPED
#        (systemctl --user stop opticfilm-eject-watch.service; start it again afterwards).
# Every scan states the gate explicitly (off|enforce), so the result does not depend on the driver default.
# Stops at the first failed scan. Abort rule: motor grinds/stalls => power OFF, then run the read-only `of135i doctor`.
# Attach the *.diag.json files (dark_b_substituted, cal_scanstop_waits) to issue #35 only when the owner says so.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
attempt=${1:?usage: ab_gate_test_upstream.sh <attempt_dir>}
cd "$ROOT" || exit 2
mkdir -p "$attempt"
ORDER=(warmup:enforce 1:off 2:enforce 3:enforce 4:off 5:off 6:enforce)
echo "Order (warm-up discarded): ${ORDER[*]}"
if [ -n "${OPTICFILM_HW_ASSUME_YES:-}" ]; then
  echo "NOTICE: OPTICFILM_HW_ASSUME_YES is set: starting WITHOUT an interactive confirmation (only for wrappers that supervise the scanner)" >&2
elif [ ! -t 0 ]; then
  echo "aborted: no confirmation (stdin is not a terminal; run it in a real terminal, or set OPTICFILM_HW_ASSUME_YES=1 in a supervised wrapper)" >&2
  exit 1
else
  read -r -p "Holder loaded and latched, watcher stopped, you are watching. Enter to start, Ctrl-C to cancel: " _ || { echo "aborted: no confirmation"; exit 1; }
fi
for item in "${ORDER[@]}"; do
  n=${item%%:*}
  gate=${item##*:}
  name="ab-$n-$gate"
  echo "=== scan $n gate=$gate ==="
  scripts/hardware/hw_run.sh "$attempt" "$name" -v scan --frame 4 --dpi 600 \
    --cal-wait-scanstop "$gate" -o "$attempt/ab-$n-$gate.tiff" >/dev/null \
    || { echo "scan $n FAILED: stopping (check the log, power-cycle if it says so)"; exit 1; }
done
echo "=== summary (warm-up excluded from the verdict) ==="
"${PYTHON:-python3}" - "$attempt" <<'PY'
import glob
import json
import os
import sys

for path in sorted(glob.glob(os.path.join(sys.argv[1], "ab-*.diag.json"))):
    name = os.path.basename(path).replace(".diag.json", "")
    diag = json.load(open(path))
    waits = diag.get("cal_scanstop_waits") or []
    rows = [w for w in waits if isinstance(w, dict)]
    longest_ms = round(1000 * max((w.get("since_clear_s") or 0 for w in rows), default=0), 1)
    multi_read = sum(1 for w in rows if (w.get("reads_100") or 0) > 1 or (w.get("reads_101") or 0) > 1)
    print(
        f"{name:22} calibration={diag.get('calibration')} "
        f"dark_b_substituted={diag.get('dark_b_substituted')} "
        f"poll_timeouts={diag.get('poll_timeouts')} scanstop_waits={len(rows)} "
        f"waits_needing_more_than_one_read={multi_read} longest_since_clear_ms={longest_ms}"
    )
PY
echo "diag JSONs are in $attempt (ab-*.diag.json)"
