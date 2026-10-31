#!/bin/bash
# One-command no-motor check of the Eject-button watcher's stop path. Run it with the scanner
# switched OFF. It renders the unit, starts it, waits for the `of135i watch` process, times the
# stop, verifies nothing is left behind and ends with a single PASS or FAIL line (exit 0 only on
# PASS). With --enable, and only after everything passed, it enables the unit (never starts it).
#
# Usage: eject-watch-check.sh [--enable] [DRIVER_DIR]     (default: $OPTICFILM_DRIVER_DIR)
#
# Test hooks (defaults are the real thing): SYSTEMCTL, JOURNALCTL, PROC_ROOT, SYSFS_USB_ROOT,
# START_WAIT_SECS (20), STOP_WARN_SECS (10), STOP_FAIL_SECS (30), STOP_WALL_SECS (130).
# Nothing here sends SIGKILL or any signal; the unit's own stop script does the stopping.
set -u

SYSTEMCTL=${SYSTEMCTL:-systemctl}
JOURNALCTL=${JOURNALCTL:-journalctl}
PROC_ROOT=${PROC_ROOT:-/proc}
SYSFS_USB_ROOT=${SYSFS_USB_ROOT:-/sys/bus/usb/devices}
START_WAIT_SECS=${START_WAIT_SECS:-20}
STOP_WARN_SECS=${STOP_WARN_SECS:-10}
STOP_FAIL_SECS=${STOP_FAIL_SECS:-30}
STOP_WALL_SECS=${STOP_WALL_SECS:-130}
UNIT=opticfilm-eject-watch.service
WATCH_PATTERN='python3 -m of135i watch'

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
started=0
stop_tried=0

usage() {
    echo "usage: eject-watch-check.sh [--enable] [DRIVER_DIR]  (default: \$OPTICFILM_DRIVER_DIR)" >&2
    exit 2
}

journal_tail() {
    "$JOURNALCTL" --user -u "$UNIT" --since "-2min" --no-pager 2>&1 | tail -20
}

stop_unit() { # bounded by the wall limit; exit status 124 means it timed out
    stop_tried=1
    timeout "$STOP_WALL_SECS" "$SYSTEMCTL" --user stop "$UNIT" >/dev/null 2>&1
}

fail() { # <message>
    echo "FAIL: $1"
    echo "--- journal tail ---"
    journal_tail
    echo "--- end journal ---"
    if ((started && !stop_tried)); then
        stop_unit || echo "note: stopping the unit did not finish cleanly"
    fi
    echo "The unit was left disabled and stopped (no SIGKILL was or will be sent)."
    echo "Keep the scanner OFF and paste this whole output to the maintainer."
    echo "FAIL"
    exit 1
}

scanner_on_bus() {
    local d vid pid
    for d in "$SYSFS_USB_ROOT"/*/; do
        [[ -r "${d}idVendor" && -r "${d}idProduct" ]] || continue
        vid=$(<"${d}idVendor")
        pid=$(<"${d}idProduct")
        [[ "$vid" == 07b3 && "$pid" == 1436 ]] && return 0
    done
    return 1
}

watcher_running() {
    local f cmd
    for f in "$PROC_ROOT"/[0-9]*/cmdline; do
        [[ -r "$f" ]] || continue
        cmd=$(tr '\0' ' ' <"$f" 2>/dev/null) || continue
        [[ "$cmd" == *"$WATCH_PATTERN"* ]] && return 0
    done
    return 1
}

enable=0
driver_dir=""
for arg in "$@"; do
    case "$arg" in
    --enable) enable=1 ;;
    -*) usage ;;
    *)
        [[ -z "$driver_dir" ]] || usage
        driver_dir=$arg
        ;;
    esac
done
driver_dir=${driver_dir:-${OPTICFILM_DRIVER_DIR:-}}
[[ -n "$driver_dir" ]] || usage

# Step 0: the check starts the watcher, which must find no scanner. Not bypassable.
if [[ ! -d "$SYSFS_USB_ROOT" ]]; then
    echo "REFUSED: cannot read the USB bus at $SYSFS_USB_ROOT, so cannot tell the scanner is off."
    exit 3
fi
if scanner_on_bus; then
    echo "REFUSED: the scanner (07b3:1436) is on the USB bus."
    echo "Switch the scanner OFF, wait until its lights go out, then run this again."
    exit 3
fi
echo "step 0: OK scanner is not on the USB bus"

# Step 1: install (renders the unit and daemon-reloads only).
"$SYSTEMCTL" --user reset-failed "$UNIT" >/dev/null 2>&1 || true
if out=$("$here/install-eject-watch.sh" "$driver_dir" 2>&1); then
    echo "step 1: OK unit installed"
else
    echo "$out"
    fail "step 1: the installer failed"
fi

# Step 2: start and wait for the watcher process.
start_ts=$(date '+%Y-%m-%d %H:%M:%S')
started=1
"$SYSTEMCTL" --user start "$UNIT" >/dev/null 2>&1 || fail "step 2: systemctl start failed"
seen=0
for ((i = 0; i < START_WAIT_SECS; i++)); do
    if watcher_running; then
        seen=1
        break
    fi
    sleep 1
done
((seen)) || fail "step 2: no '$WATCH_PATTERN' process appeared within ${START_WAIT_SECS}s"
echo "step 2: OK watcher process seen"

# Step 3: time the stop.
t0=$SECONDS
stop_unit
rc=$?
elapsed=$((SECONDS - t0))
((rc != 124)) || fail "step 3: stop did not finish within ${STOP_WALL_SECS}s"
((elapsed <= STOP_FAIL_SECS)) || fail "step 3: stop took ${elapsed}s (limit ${STOP_FAIL_SECS}s)"
if ((elapsed > STOP_WARN_SECS)); then
    echo "step 3: WARN stop took ${elapsed}s (expected under ${STOP_WARN_SECS}s)"
else
    echo "step 3: OK stop took ${elapsed}s"
fi

# Step 4: verify.
if watcher_running; then fail "step 4: a watcher process is still running after the stop"; fi
echo "step 4: OK no watcher process remains"
state=$("$SYSTEMCTL" --user is-active "$UNIT" 2>&1)
[[ "$state" == inactive ]] || fail "step 4: unit state is '$state', expected exactly 'inactive'"
echo "step 4: OK unit state is inactive"
if "$JOURNALCTL" --user -u "$UNIT" --since "$start_ts" --no-pager 2>&1 | grep -Eiq 'SIGKILL|killed'; then
    fail "step 4: the journal shows SIGKILL or 'killed'"
fi
echo "step 4: OK no SIGKILL in the journal"

# Step 5: enable (only), never start.
if ((enable)); then
    "$SYSTEMCTL" --user enable "$UNIT" >/dev/null 2>&1 || fail "step 5: systemctl enable failed"
    echo "step 5: OK enabled (not started). Switch the scanner on and wait one to two minutes."
else
    echo "step 5: not enabled. To enable it: systemctl --user enable $UNIT"
fi
echo "PASS"
exit 0
