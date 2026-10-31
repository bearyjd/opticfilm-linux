#!/bin/bash
# Installs the Eject-button watcher unit for the current user. It renders a unit template, writes
# it to ~/.config/systemd/user and runs `systemctl --user daemon-reload`. It does NOT enable or
# start the unit.
#
# Usage: install-eject-watch.sh [--runner auto|host|distrobox[:NAME]] [driver_dir]
#        (driver_dir defaults to $OPTICFILM_DRIVER_DIR)
#
# Runners (same names as the app's OPTICFILM_DRIVER_RUNNER):
#   host           the host python3 runs `python3 -m of135i watch` itself; needs pyusb on it.
#   distrobox[:N]  the watcher runs in distrobox container N (default dev) through distrobox-enter.
#   auto (default) host if the host python3 can import pyusb, else distrobox:dev if distrobox-enter
#                  exists, else an error.
#
# Every path ends up in the unit as a bare word (distrobox-enter even evals them), so each must
# be absolute and made only of letters, digits and _ . / - (no spaces, %, $, quotes or the like).
# OF135I_DEPS_OVERLAY (optional) adds a folder of extra Python packages to the host unit's
# PYTHONPATH. EJECT_WATCH_UNIT_DIR is a test hook, gated by EJECT_WATCH_CHECK_TESTING=1.
set -euo pipefail

die() {
    echo "install-eject-watch: $*" >&2
    exit 1
}

check_plain() { # <what> <path>: absolute and only plain characters
    local what=$1 path=$2
    [[ -n "$path" ]] || die "$what is empty"
    # LC_ALL=C: in a UTF-8 locale a range like A-Z can also match letters such as e-acute.
    LC_ALL=C bash -c '[[ $1 =~ ^[A-Za-z0-9_./-]+$ ]]' _ "$path" ||
        die "$what has characters that are not allowed in the unit (only letters, digits and _ . / - are): '$path'"
    [[ "$path" == /* ]] || die "$what must be an absolute path: '$path'"
}

check_path() { # <what> <path>: a plain, absolute folder
    check_plain "$1" "$2"
    [[ -d "$2" ]] || die "$1 is not a folder: '$2'"
}

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
script_dir=$(cd -- "$here/../packaging/systemd" && pwd)
runner=auto
driver_dir=""
while (($#)); do
    case "$1" in
    --runner)
        (($# >= 2)) || die "--runner needs a value (auto, host or distrobox[:NAME])"
        runner=$2
        shift 2
        ;;
    --runner=*)
        runner=${1#--runner=}
        shift
        ;;
    -*) die "unknown option: $1" ;;
    *)
        [[ -z "$driver_dir" ]] || die "only one driver folder may be given"
        driver_dir=$1
        shift
        ;;
    esac
done
driver_dir=${driver_dir:-${OPTICFILM_DRIVER_DIR:-}}
[[ -n "$driver_dir" ]] || die "give the driver folder as an argument or set OPTICFILM_DRIVER_DIR"
driver_dir=${driver_dir%/}

PYUSB_HINT="install pyusb for the host python3 (Fedora: sudo dnf install python3-pyusb; otherwise: python3 -m pip install --user pyusb), or use --runner distrobox"

check_path "the script folder" "$script_dir"
check_path "the driver folder" "$driver_dir"
driver_dir=$(realpath -- "$driver_dir") || die "cannot resolve the driver folder"
check_path "the resolved driver folder" "$driver_dir"
for name in eject-watch-run.sh eject-watch-stop.sh; do
    [[ -x "$script_dir/$name" ]] || die "$script_dir/$name is missing or not executable"
done

# The host unit's PYTHONPATH: the driver folder, plus an optional folder of extra packages.
pythonpath=$driver_dir
if [[ -n "${OF135I_DEPS_OVERLAY:-}" ]]; then
    check_path "OF135I_DEPS_OVERLAY" "${OF135I_DEPS_OVERLAY%/}"
    pythonpath=$driver_dir:${OF135I_DEPS_OVERLAY%/}
fi

python_works() { # <python>: imports as the host unit will run it (clean environment, its PYTHONPATH)
    check_plain "the python3 path" "$1"
    (cd -- "$driver_dir" && env -i PATH=/usr/local/bin:/usr/bin:/bin PYTHONPATH="$pythonpath" "$1" -c 'import usb; import of135i.cli') >/dev/null 2>&1
}

# Same match logic as the app (src/opticfilm_linux/app/eject_watch.py); unreadable /proc = "yes".
PYHELPER='import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from opticfilm_linux.app.eject_watch import watch_process_running
print("yes" if watch_process_running(Path(sys.argv[2])) else "no")'

container=dev
python_path=""
reason=""
case "$runner" in
auto)
    python_path=$(command -v python3 || true)
    if [[ -n "$python_path" ]] && python_works "$python_path"; then
        runner=host
        reason="$python_path imports pyusb and of135i.cli as the unit will run it"
    elif command -v distrobox-enter >/dev/null 2>&1; then
        runner=distrobox
        reason="the host python3 cannot import pyusb and of135i.cli, and distrobox-enter exists"
    else
        die "no way to run the watcher: the host python3 cannot import pyusb and of135i.cli (PYTHONPATH=$pythonpath) and distrobox-enter was not found. $PYUSB_HINT"
    fi
    ;;
host)
    python_path=$(command -v python3 || true)
    [[ -n "$python_path" ]] || die "python3 was not found on PATH"
    python_works "$python_path" || die "$python_path cannot import pyusb and of135i.cli with PYTHONPATH=$pythonpath (cannot import pyusb?): $PYUSB_HINT"
    reason="requested"
    ;;
distrobox | distrobox:*)
    if [[ "$runner" == distrobox:* ]]; then
        container=${runner#distrobox:}
        LC_ALL=C bash -c '[[ $1 =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]' _ "$container" ||
            die "the container name is not allowed (letters, digits, _ . - and not starting with . - _): '$container'"
    fi
    runner=distrobox
    reason="requested"
    ;;
*) die "unknown runner '$runner' (use auto, host, distrobox or distrobox:NAME)" ;;
esac

if [[ "$runner" == host ]]; then
    template=$script_dir/opticfilm-eject-watch-host.service
    echo "runner: host ($reason)"
    echo "host-python: $python_path"
    echo "host-pythonpath: $pythonpath"
else
    template=$script_dir/opticfilm-eject-watch.service
    pythonpath=$driver_dir
    echo "runner: distrobox:$container ($reason)"
fi
[[ -f "$template" ]] || die "unit template not found: $template"

unit_dir=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
if [[ "${EJECT_WATCH_CHECK_TESTING:-}" == 1 ]]; then
    unit_dir=${EJECT_WATCH_UNIT_DIR:-$unit_dir}
elif [[ -n "${EJECT_WATCH_UNIT_DIR:-}" ]]; then
    echo "note: ignoring the test override EJECT_WATCH_UNIT_DIR (EJECT_WATCH_CHECK_TESTING is not 1)"
fi
unit=$unit_dir/opticfilm-eject-watch.service

content=$(<"$template")
content=${content//@SCRIPT_DIR@/$script_dir}
content=${content//@DRIVER_DIR@/$driver_dir}
content=${content//@PYTHONPATH@/$pythonpath}
content=${content//@PYTHON@/$python_path}
content=${content//@CONTAINER@/$container}
if grep -q '@[A-Z_]*@' <<<"$content"; then
    die "a placeholder was left in the rendered unit"
fi

mkdir -p -- "$unit_dir"
new=$(mktemp)
trap 'rm -f -- "$new"' EXIT
printf '%s\n' "$content" >"$new"

# Both runners share one unit name. Rewriting the unit under a running watcher of the other
# runner would leave it alive where `stop` cannot reach it, so refuse unless nothing changes.
if ! { [[ -f "$unit" ]] && cmp -s -- "$unit" "$new"; }; then
    state=$(timeout -k 2 20 systemctl --user is-active opticfilm-eject-watch.service 2>/dev/null || true)
    [[ -n "$state" ]] || die "cannot determine the unit state (systemctl failed or timed out)"
    case "$state" in
    active | activating | deactivating | reloading)
        die "the unit is $state and would be changed: stop it first with 'systemctl --user stop opticfilm-eject-watch.service', make sure no of135i watch process is left, then run this again"
        ;;
    esac
    proc_root=/proc
    if [[ "${EJECT_WATCH_CHECK_TESTING:-}" == 1 ]]; then proc_root=${PROC_ROOT:-/proc}; fi # tests only
    helper_python=$(command -v python3 || true)
    if [[ -n "$helper_python" ]]; then
        found=$("$helper_python" -I -c "$PYHELPER" "$script_dir/../../src" "$proc_root" 2>/dev/null || true)
        if [[ "$found" != no ]]; then
            die "an of135i watch process is running (or /proc cannot be read), so the unit will not be changed: end it with Ctrl-C in its terminal or kill -INT <pid> (never kill -9), then run this again"
        fi
    else
        echo "note: python3 not found, so could not look for a running of135i watch process" >&2
    fi
fi
if [[ -f "$unit" ]]; then
    if cmp -s -- "$unit" "$new"; then
        echo "unit already up to date: $unit"
    else
        echo "replacing $unit; changes:"
        diff -u -- "$unit" "$new" || true
    fi
else
    echo "creating $unit"
fi
install -m 0644 -- "$new" "$unit" # not mktemp's 0600, and an old 0600 unit is fixed too

if command -v systemctl >/dev/null 2>&1; then
    timeout -k 2 20 systemctl --user daemon-reload
    echo "ran: systemctl --user daemon-reload"
else
    echo "systemctl not found; run 'systemctl --user daemon-reload' yourself" >&2
fi
echo "not enabled or started. To do that: systemctl --user enable --now opticfilm-eject-watch.service"
