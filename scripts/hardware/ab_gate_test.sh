#!/bin/bash
# Interleaved A/B of the calibration stop gate. Run it YOURSELF in a real terminal, watching the scanner.
# usage: ab_gate_test.sh <attempt_dir>       (e.g. spike/b-ab-<date>)
# Run it inside whatever environment has pyusb; with a distrobox container that is
#   distrobox enter <name> -- bash -lc 'cd <repo> && scripts/hardware/ab_gate_test.sh <attempt_dir>'
# Needs OPTICFILM_DRIVER_DIR (see README.md in this folder). Needs a real terminal for the confirmation;
# OPTICFILM_HW_ASSUME_YES=1 skips it and is for wrappers that supervise the scanner only.
# Needs: scanner powered on, holder LOADED and latched (run `load` first, see docs), idle-homed.
# Every scan states the gate explicitly (off|enforce), so the result does not depend on the driver default.
# Order is balanced (A B B A A B; A = gate off, B = gate on) so linear warm-up drift cancels better than A B A B A B.
# Scan 0 is a discarded warm-up (gate on). Stops at the first failed scan. Abort rule: motor grinds/stalls => power OFF.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
attempt=${1:?usage: ab_gate_test.sh <attempt_dir>}
cd "$ROOT" || exit 2; mkdir -p "$attempt"
run() { scripts/hardware/hw_run.sh "$attempt" "$@"; }
ORDER=(warmup:enforce 1:off 2:enforce 3:enforce 4:off 5:off 6:enforce)
echo "Order (warmup discarded): ${ORDER[*]}"
if [ -n "${OPTICFILM_HW_ASSUME_YES:-}" ]; then
  echo "NOTICE: OPTICFILM_HW_ASSUME_YES is set: starting WITHOUT an interactive confirmation (only for wrappers that supervise the scanner)" >&2
elif [ ! -t 0 ]; then
  echo "aborted: no confirmation (stdin is not a terminal; run it in a real terminal, or set OPTICFILM_HW_ASSUME_YES=1 in a supervised wrapper)" >&2
  exit 1
else
  read -r -p "Holder loaded and latched, you are watching. Enter to start, Ctrl-C to cancel: " _ || { echo "aborted: no confirmation"; exit 1; }
fi
for item in "${ORDER[@]}"; do
  n=${item%%:*}; gate=${item##*:}; name="ab-$n-$gate"
  echo "=== scan $n gate=$gate ==="
  run "$name" -v scan --slide 4 --dpi 600 --ir --no-clean --dark-fallback default \
      --cal-wait-scanstop "$gate" -o "$attempt/ab-$n.tiff" >/dev/null || { echo "scan $n FAILED: stopping (check the log, power-cycle if it says so)"; exit 1; }
done
echo "=== summary (cal_integrity per scan; warmup excluded from the verdict) ==="
"${PYTHON:-python3}" - "$attempt" <<'PY'
import glob, json, os, sys
d = sys.argv[1]
for p in sorted(glob.glob(os.path.join(d, "ab-*-f4.diag.json"))):
    j = json.load(open(p)); name = os.path.basename(p).replace("-f4.diag.json", "")
    w = j.get("cal_scanstop_waits", [])
    print(f"{name:18} integrity={j.get('cal_integrity')} reasons={j.get('cal_integrity_reasons')} "
          f"poll_timeouts={j.get('poll_timeouts')} waits={len(w)}")
PY
