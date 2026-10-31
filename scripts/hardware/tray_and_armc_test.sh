#!/bin/bash
# Supervised hardware phases. Run it YOURSELF in a real terminal, watching the scanner.
# Run it inside whatever environment has pyusb; with a distrobox container that is
#   distrobox enter <name> -- bash -lc 'cd <repo> && scripts/hardware/tray_and_armc_test.sh <phase> <attempt_dir>'
# Needs OPTICFILM_DRIVER_DIR (see README.md in this folder). Needs a real terminal for the confirmation;
# OPTICFILM_HW_ASSUME_YES=1 skips it and is for wrappers that supervise the scanner only.
# phases:
#   tray   full four-slide tray at 3600 dpi, stop gate on (default). One scan, ~minutes. Roadmap item 2.
#   armc   arm C proof at 600 dpi slide 4: bufempty on with the stop gate OFF (isolates C), then stop gate on as control.
#          Order C,ctl,C,ctl (2 each). Roadmap item 3. Look at cal_bufempty_waits and cal_integrity in the summary.
# Needs: scanner on, holder LOADED and latched, idle-homed. Abort rule: motor grinds/stalls => power OFF.
# Stops at the first failed scan. Nothing is retried.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase=${1:?usage: tray_and_armc_test.sh <tray|armc> <attempt_dir>}
attempt=${2:?usage: tray_and_armc_test.sh <tray|armc> <attempt_dir>}
cd "$ROOT" || exit 2; mkdir -p "$attempt"
run() { scripts/hardware/hw_run.sh "$attempt" "$@"; }
if [ -n "${OPTICFILM_HW_ASSUME_YES:-}" ]; then
  echo "NOTICE: OPTICFILM_HW_ASSUME_YES is set: starting WITHOUT an interactive confirmation (only for wrappers that supervise the scanner)" >&2
elif [ ! -t 0 ]; then
  echo "aborted: no confirmation (stdin is not a terminal; run it in a real terminal, or set OPTICFILM_HW_ASSUME_YES=1 in a supervised wrapper)" >&2
  exit 1
else
  read -r -p "Phase '$phase': holder loaded and latched, you are watching. Enter to start, Ctrl-C to cancel: " _ || { echo "aborted: no confirmation"; exit 1; }
fi

case "$phase" in
  tray)
    run tray3600 -v scan --slides 1-4 --dpi 3600 --ir --no-clean --dark-fallback default \
        --cal-wait-scanstop enforce -o "$attempt/tray.tiff" >/dev/null \
      || { echo "tray scan FAILED: stopping (read the log; power-cycle if it says so)"; exit 1; }
    ;;
  armc)
    for item in C1:off:enforce ctl1:enforce:off C2:off:enforce ctl2:enforce:off; do
      IFS=: read -r n stop buf <<<"$item"
      echo "=== $n scanstop=$stop bufempty=$buf ==="
      run "armc-$n" -v scan --slide 4 --dpi 600 --ir --no-clean --dark-fallback default \
          --cal-wait-scanstop "$stop" --cal-wait-bufempty "$buf" -o "$attempt/armc-$n.tiff" >/dev/null \
        || { echo "scan $n FAILED: stopping"; exit 1; }
    done
    ;;
  *) echo "unknown phase '$phase'"; exit 2 ;;
esac

echo "=== summary ==="
"${PYTHON:-python3}" - "$attempt" <<'PY'
import glob, json, os, sys
for p in sorted(glob.glob(os.path.join(sys.argv[1], "*.diag.json"))):
    j = json.load(open(p)); name = os.path.basename(p).replace(".diag.json", "")
    print(f"{name:22} integrity={j.get('cal_integrity')} poll_timeouts={j.get('poll_timeouts')} "
          f"scanstop_waits={len(j.get('cal_scanstop_waits', []))} bufempty_waits={len(j.get('cal_bufempty_waits') or [])}")
PY
