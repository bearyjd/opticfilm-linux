#!/bin/bash
# One-command check of the Eject-button watcher unit. It ends with a single PASS, FAIL or
# INCONCLUSIVE line. Exit codes: 0 PASS, 1 FAIL, 2 usage, 3 refused (scanner state wrong or
# unknown), 4 refused (an `of135i watch` already runs, or /proc cannot be read), 5 INCONCLUSIVE.
#
# Usage: eject-watch-check.sh [--live] [--enable] [--runner auto|host|distrobox[:NAME]] [DRIVER_DIR]
#        (DRIVER_DIR defaults to $OPTICFILM_DRIVER_DIR; --runner is passed to the installer and
#        defaults to auto: host python3 with pyusb if it has it, else distrobox:dev)
#
# Default mode (scanner OFF, lights out): proves the start path through distrobox-enter works and
# that the stop from the failed/restarting state is clean and fast. It does NOT prove the SIGINT
# stop of a live watcher: with the scanner off `watch` exits within a second.
# --live (scanner ON, idle-homed; do NOT press the Eject button during it): the watcher must stay
# up for 10 s, then be stopped cleanly. This is the only mode that proves the live stop path.
# The script never sends a command to the scanner, presses or simulates a button, or sends any
# signal. --enable enables the unit (never starts it) only after a full PASS of the mode that ran.
#
# Test hooks: the variables listed in OVERRIDES are honoured ONLY when EJECT_WATCH_CHECK_TESTING=1;
# otherwise they are ignored (with a note) and the real paths and timings are used.
set -u

OVERRIDES="SYSTEMCTL JOURNALCTL PROC_ROOT SYSFS_USB_ROOT CGROUP_ROOT START_WAIT_SECS SETTLE_SECS
LIVE_HOLD_SECS STOP_WARN_SECS STOP_FAIL_SECS STOP_WALL_SECS CGROUP_WAIT_SECS"
if [[ "${EJECT_WATCH_CHECK_TESTING:-}" != 1 ]]; then
    for v in $OVERRIDES; do
        if [[ -n "${!v:-}" ]]; then
            echo "note: ignoring the test override $v (EJECT_WATCH_CHECK_TESTING is not 1)"
            unset "$v"
        fi
    done
fi
SYSTEMCTL=${SYSTEMCTL:-systemctl}
JOURNALCTL=${JOURNALCTL:-journalctl}
PROC_ROOT=${PROC_ROOT:-/proc}
SYSFS_USB_ROOT=${SYSFS_USB_ROOT:-/sys/bus/usb/devices}
CGROUP_ROOT=${CGROUP_ROOT:-/sys/fs/cgroup}
START_WAIT_SECS=${START_WAIT_SECS:-20}
SETTLE_SECS=${SETTLE_SECS:-2}
LIVE_HOLD_SECS=${LIVE_HOLD_SECS:-10}
STOP_WARN_SECS=${STOP_WARN_SECS:-10}
STOP_FAIL_SECS=${STOP_FAIL_SECS:-30}
STOP_WALL_SECS=${STOP_WALL_SECS:-130}
CGROUP_WAIT_SECS=${CGROUP_WAIT_SECS:-5}
UNIT=opticfilm-eject-watch.service
# Positive evidence is the driver's own startup output only; any failure signature wins over it.
EVIDENCE_RE='not found on the host USB bus|watching for button events|opened device'
BAD_START_RE='null directory|syntax error|No such file|command not found|Permission denied|Traceback|ModuleNotFoundError|ImportError|No module named|SyntaxError|status=127|status=203'
BAD_STOP_RE='SIGKILL|status=9/KILL|code=killed'

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
helper_src=$here/../src
# Same match logic as the app (src/opticfilm_linux/app/eject_watch.py); unreadable /proc = "yes".
PYHELPER='import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from opticfilm_linux.app.eject_watch import watch_process_running
print("yes" if watch_process_running(Path(sys.argv[2])) else "no")'

probe_host_python() { # <python> <pythonpath> <driver dir>: imports as the host unit will run it
    (cd -- "$3" && env -i PATH=/usr/local/bin:/usr/bin:/bin PYTHONPATH="$2" "$1" -c 'import usb; import of135i.cli') >/dev/null 2>&1
}

enabled_before=unknown
started=0
stop_tried=0
reported=0
start_ts=""

usage() {
    echo "usage: eject-watch-check.sh [--live] [--enable] [--runner auto|host|distrobox[:NAME]] [DRIVER_DIR]" >&2
    echo "       (DRIVER_DIR defaults to \$OPTICFILM_DRIVER_DIR; --runner defaults to auto)" >&2
    exit 2
}

sc_wait() { # <secs> <verb> [args]: bounded; background + wait so a signal's trap runs at once
    local secs=$1
    shift
    timeout "$secs" "$SYSTEMCTL" --user "$@" >/dev/null 2>&1 &
    wait $!
}

sc_out() { # <verb> [args]: stdout and stderr of a quick systemctl call
    timeout 20 "$SYSTEMCTL" --user "$@" 2>&1
}

journal_tail() {
    "$JOURNALCTL" --user -u "$UNIT" --since "-2min" --no-pager 2>&1 | tail -20
}

journal_since_start() {
    "$JOURNALCTL" --user -u "$UNIT" --since "$start_ts" --no-pager 2>&1
}

unit_state() {
    local s
    s=$(sc_out is-active "$UNIT")
    echo "${s:-unknown}"
}

is_running_state() { [[ "$1" == active || "$1" == activating || "$1" == deactivating || "$1" == reloading ]]; }

stop_unit() {
    stop_tried=1
    sc_wait "$STOP_WALL_SECS" stop "$UNIT"
}

watcher_status() { # yes | no | unknown
    local out
    out=$(python3 -I -c "$PYHELPER" "$helper_src" "$PROC_ROOT" 2>/dev/null) || out=""
    case "$out" in
    yes | no) echo "$out" ;;
    *) echo unknown ;;
    esac
}


cleanup_report() { # never claims a state it did not read
    local state enabled wp
    echo "--- journal tail ---"
    journal_tail
    echo "--- end journal ---"
    if ((started && !stop_tried)); then stop_unit; fi
    state=$(unit_state)
    if is_running_state "$state"; then stop_unit; fi
    sc_wait 30 disable "$UNIT"
    state=$(unit_state)
    enabled=$(sc_out is-enabled "$UNIT" | head -1)
    wp=$(watcher_status)
    echo "final state: is-active=$state, is-enabled=${enabled:-unknown}, watcher process=$wp"
    if is_running_state "$state" || [[ "$wp" != no ]]; then
        echo "STILL RUNNING (or not known to be stopped): the unit or an of135i watch process may be alive."
        echo "End it with Ctrl-C in its terminal or kill -INT <pid>; never kill -9."
    fi
    if [[ "$enabled" == enabled ]]; then
        echo "The unit is still ENABLED; run: systemctl --user disable $UNIT"
    fi
    if [[ "$enabled_before" == enabled && "$enabled" != enabled ]]; then
        echo "The unit was enabled before this check and is now disabled; re-enable it yourself if you want it."
    fi
}

fail() { # <message>
    reported=1
    echo "FAIL: $1"
    cleanup_report
    echo "Keep the scanner as it is (do not touch it) and paste this whole output."
    echo "FAIL"
    exit 1
}

inconclusive() { # <message>
    reported=1
    echo "INCONCLUSIVE ($1); the live stop path was NOT tested"
    echo "--- journal lines since the start ---"
    journal_since_start | tail -8
    cleanup_report
    echo "INCONCLUSIVE"
    exit 5
}

# shellcheck disable=SC2329 # run by trap
on_signal() {
    trap '' INT TERM HUP
    fail "interrupted by a signal; stopping the unit (the script sends no signals itself)"
}

# shellcheck disable=SC2329 # run by trap
on_exit() {
    local rc=$?
    if ((started && !reported)); then
        reported=1
        echo "FAIL: the script ended unexpectedly (status $rc)"
        cleanup_report
        echo "FAIL"
    fi
}
trap on_signal INT TERM HUP
trap on_exit EXIT

scanner_bus_state() { # present | absent | unknown (fail closed: anything unreadable is unknown)
    local d vid pid seen=0
    shopt -s nullglob
    local -a entries=("$SYSFS_USB_ROOT"/*/)
    shopt -u nullglob
    if ((${#entries[@]} == 0)); then
        echo unknown
        return
    fi
    for d in "${entries[@]}"; do
        [[ -e "${d}idVendor" ]] || continue # interfaces have no idVendor
        vid=$(cat -- "${d}idVendor" 2>/dev/null) || { echo unknown; return; }
        [[ -e "${d}idProduct" ]] || { echo unknown; return; }
        pid=$(cat -- "${d}idProduct" 2>/dev/null) || { echo unknown; return; }
        seen=$((seen + 1))
        if [[ "$vid" == 07b3 && "$pid" == 1436 ]]; then
            echo present
            return
        fi
    done
    if ((seen)); then echo absent; else echo unknown; fi
}

wait_for_process() { # polls up to START_WAIT_SECS; fails on a previous-failure signature
    local i
    for ((i = 0; i < START_WAIT_SECS; i++)); do
        if journal_since_start | grep -Eiq "$BAD_START_RE"; then
            fail "step 2: the journal shows a start-path failure signature ($BAD_START_RE)"
        fi
        if [[ $(watcher_status) == yes ]]; then return 0; fi
        sleep 1
    done
    return 1
}

step_start_off_mode() {
    local i seen=0 evidence=0 j state ts
    for ((i = 0; i < START_WAIT_SECS; i++)); do
        [[ $(watcher_status) == yes ]] && seen=1
        j=$(journal_since_start)
        if grep -Eiq "$BAD_START_RE" <<<"$j"; then
            fail "step 2: the journal shows a start-path failure signature ($BAD_START_RE)"
        fi
        if ((seen)) || grep -Eiq "$EVIDENCE_RE" <<<"$j"; then
            evidence=1
            break
        fi
        sleep 1
    done
    if ((!evidence)); then
        ts=$(sc_out show -p ExecMainStartTimestamp -p ExecMainStatus "$UNIT" | grep '^ExecMainStartTimestamp=.' || true)
        [[ -n "$ts" ]] || fail "step 2: no sign that ExecStart ran: no driver output in the journal and no ExecMainStartTimestamp"
        echo "step 2: note: ExecStart ran ($ts) but the journal has no driver output yet"
    fi
    sleep "$SETTLE_SECS"
    if journal_since_start | grep -Eiq "$BAD_START_RE"; then
        fail "step 2: the journal shows a start-path failure signature ($BAD_START_RE)"
    fi
    if ((seen)); then
        state=$(unit_state)
        is_running_state "$state" || fail "step 2: a watcher process exists but the unit is '$state' (not started by the unit?)"
    fi
    echo "step 2: OK the start path ran through the container (watcher process seen: $seen)"
}

step_start_live() {
    local i state pid cmd
    wait_for_process || inconclusive "the watcher process never appeared within ${START_WAIT_SECS}s"
    for ((i = 0; i < LIVE_HOLD_SECS; i++)); do
        sleep 1
        [[ $(watcher_status) == yes ]] || inconclusive "watcher refused to run: scanner not idle-homed, or it exited by itself"
    done
    if journal_since_start | grep -Eiq "$BAD_START_RE"; then
        fail "step 2: the journal shows a start-path failure signature ($BAD_START_RE)"
    fi
    state=$(unit_state)
    is_running_state "$state" || fail "step 2: the watcher is up but the unit is '$state'"
    pid=$(sc_out show -p MainPID "$UNIT" | sed -n 's/^MainPID=//p')
    if [[ -z "$pid" || "$pid" == 0 || ! -d "$PROC_ROOT/$pid" ]]; then
        fail "step 2: the unit has no live MainPID (got '${pid:-none}'); the watcher is not tied to the unit"
    fi
    if [[ "$runner_kind" == host ]]; then # the host unit's main process IS the watcher
        cmd=$(tr '\0' ' ' <"$PROC_ROOT/$pid/cmdline" 2>/dev/null || true)
        if [[ "$cmd" != *of135i*" watch "* ]]; then
            fail "step 2: MainPID $pid is not 'of135i watch' (command line: '$cmd'); the watcher is not tied to the unit"
        fi
    fi
    echo "step 2: OK the watcher stayed up ${LIVE_HOLD_SECS}s under the unit (MainPID $pid)"
}

cgroup_dir() {
    local cg
    cg=$(sc_out show -p ControlGroup "$UNIT" | sed -n 's/^ControlGroup=//p')
    [[ -n "$cg" ]] && echo "$CGROUP_ROOT$cg"
}

step_cgroup_clear() { # <cgroup dir or empty>
    local dir=$1 i
    if [[ -z "$dir" ]]; then
        echo "step 4: note: no ControlGroup known, skipped the leftover conmon/podman check"
        return
    fi
    for ((i = 0; i <= CGROUP_WAIT_SECS; i++)); do
        if [[ ! -e "$dir/cgroup.procs" ]]; then
            echo "step 4: OK the unit's cgroup is gone"
            return
        fi
        if [[ ! -r "$dir/cgroup.procs" ]]; then
            echo "step 4: note: cgroup.procs not readable, skipped the leftover conmon/podman check"
            return
        fi
        if [[ -z "$(<"$dir/cgroup.procs")" ]]; then
            echo "step 4: OK no process left in the unit's cgroup"
            return
        fi
        ((i < CGROUP_WAIT_SECS)) && sleep 1
    done
    fail "step 4: processes remain in the unit's cgroup ${CGROUP_WAIT_SECS}s after the stop: $(tr '\n' ' ' <"$dir/cgroup.procs")"
}

step_stop_and_verify() {
    local cg t0 rc elapsed state
    cg=$(cgroup_dir)
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
    if [[ $(watcher_status) != no ]]; then fail "step 4: a watcher process is still running (or /proc is unreadable) after the stop"; fi
    echo "step 4: OK no watcher process remains"
    state=$(unit_state)
    [[ "$state" == inactive ]] || fail "step 4: unit state is '$state', expected exactly 'inactive'"
    echo "step 4: OK unit state is inactive"
    if journal_since_start | grep -Eiq "$BAD_STOP_RE"; then
        fail "step 4: this run's journal shows SIGKILL / status=9/KILL / code=killed"
    fi
    echo "step 4: OK no SIGKILL in this run's journal"
    step_cgroup_clear "$cg"
}

live=0
enable=0
driver_dir=""
runner_req=auto
runner_kind=unknown
while (($#)); do
    case "$1" in
    --live) live=1 ;;
    --enable) enable=1 ;;
    --runner)
        (($# >= 2)) || usage
        runner_req=$2
        shift
        ;;
    --runner=*) runner_req=${1#--runner=} ;;
    -*) usage ;;
    *)
        [[ -z "$driver_dir" ]] || usage
        driver_dir=$1
        ;;
    esac
    shift
done
driver_dir=${driver_dir:-${OPTICFILM_DRIVER_DIR:-}}
[[ -n "$driver_dir" ]] || usage

# Step 0: the scanner state must match the mode. Not bypassable by a flag or the environment.
bus=$(scanner_bus_state)
if ((live)); then
    if [[ "$bus" != present ]]; then
        echo "REFUSED: --live needs the scanner ON the USB bus (state: $bus). Switch it on, wait for it to settle, run again."
        exit 3
    fi
    echo "step 0: OK scanner is on the USB bus. Do NOT press the Eject button during this test."
else
    if [[ "$bus" != absent ]]; then
        if [[ "$bus" == present ]]; then
            echo "REFUSED: the scanner (07b3:1436) is on the USB bus."
            echo "Switch the scanner OFF, wait until its lights go out, then run this again."
        else
            echo "REFUSED: cannot tell whether the scanner is off (USB bus unreadable or empty at $SYSFS_USB_ROOT)."
        fi
        exit 3
    fi
    echo "step 0: OK scanner is not on the USB bus"
fi
if [[ $(watcher_status) != no ]]; then
    echo "REFUSED: an of135i watch process is already running (or /proc cannot be read)."
    echo "End it first (Ctrl-C in its terminal or kill -INT <pid>, never kill -9), then run this again."
    exit 4
fi
echo "step 0: OK no other of135i watch process"

# Step 1: install (renders the unit and daemon-reloads only).
enabled_before=$(sc_out is-enabled "$UNIT" | head -1)
sc_wait 30 reset-failed "$UNIT"
if out=$("$here/install-eject-watch.sh" --runner "$runner_req" "$driver_dir" 2>&1); then
    runner_kind=$(sed -n 's/^runner: \([^ ]*\).*/\1/p' <<<"$out" | head -1)
    runner_kind=${runner_kind:-unknown}
    echo "step 1: OK unit installed (runner: $runner_kind)"
else
    echo "$out"
    fail "step 1: the installer failed"
fi
if [[ "$runner_kind" == host ]]; then
    host_py=$(sed -n 's/^host-python: //p' <<<"$out" | head -1)
    host_pp=$(sed -n 's/^host-pythonpath: //p' <<<"$out" | head -1)
    if [[ -n "$host_py" && -n "$host_pp" ]]; then
        probe_host_python "$host_py" "$host_pp" "$driver_dir" ||
            fail "step 1: $host_py cannot import usb and of135i.cli the way the unit runs it (PYTHONPATH=$host_pp)"
        echo "step 1: OK the host python imports usb and of135i.cli as the unit will run it"
    else
        echo "step 1: note: the installer did not report the host python; skipped the import probe"
    fi
fi

# Step 2: start.
start_ts=$(date '+%Y-%m-%d %H:%M:%S')
started=1
sc_wait 60 start "$UNIT" || fail "step 2: systemctl start failed"
if ((live)); then step_start_live; else step_start_off_mode; fi

# Steps 3-4: stop and verify.
step_stop_and_verify

# Step 5: enable (only), never start.
if ((enable)); then
    sc_wait 30 enable "$UNIT" || fail "step 5: systemctl enable failed"
    echo "step 5: OK enabled (not started). Switch the scanner on and wait one to two minutes."
else
    echo "step 5: not enabled. To enable it: systemctl --user enable $UNIT"
fi
reported=1
if ((live)); then
    echo "PASS (--live mode: the watcher stayed up ${LIVE_HOLD_SECS}s and its stop was clean)"
else
    echo "PASS (scanner-off mode: start path and clean stop from the restarting unit; the live stop path is only proven by --live)"
fi
exit 0
