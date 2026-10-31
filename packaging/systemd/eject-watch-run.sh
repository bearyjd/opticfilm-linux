#!/bin/bash
# Runs the driver's `of135i watch` in the foreground. The systemd unit starts this INSIDE the
# distrobox container, as `distrobox-enter ... -- <this script> <driver_dir>`.
#
# Usage: eject-watch-run.sh <driver_dir>
#
# This is a script, not an inline `bash -c '...'`, because distrobox-enter joins its arguments
# into one string and evals it, so quotes and shell syntax in them are lost.
set -u

dir=${1:-}
if [[ -z "$dir" || ! -d "$dir" ]]; then
    echo "eject-watch-run: driver directory not found: '${dir}'" >&2
    echo "usage: eject-watch-run.sh <driver_dir>" >&2
    exit 2
fi

cd -- "$dir" || exit 2
PYTHONPATH=. exec python3 -m of135i watch
