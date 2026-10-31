#!/bin/bash
# Stops the driver's `of135i watch` from inside the distrobox container, with SIGINT only, and
# waits for it to exit. The systemd unit runs this as ExecStop.
#
# Exit 0: nothing was running, or it exited. Exit 1: it was still running after the wait.
# It is never killed: a kill during an eject can land mid-move.
#
# The wait is EJECT_WATCH_STOP_LOOPS polls (default 100) EJECT_WATCH_STOP_DELAY seconds apart
# (default 1), under the unit's TimeoutStopSec.
set -u

# The bracket keeps the pattern from matching this script's own command line.
pattern='[o]f135i( .*)? watch( |$)'
loops=${EJECT_WATCH_STOP_LOOPS:-100}
delay=${EJECT_WATCH_STOP_DELAY:-1}

pkill -INT -f "$pattern" || exit 0 # pkill exits 1 when nothing matched

for ((i = 0; i < loops; i++)); do
    pgrep -f "$pattern" >/dev/null || exit 0
    sleep "$delay"
done

echo "eject-watch-stop: of135i watch is still running after the wait; it was not killed" >&2
exit 1
