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
# PYTHONPATH. Set EJECT_WATCH_UNIT_DIR to write somewhere other than ~/.config/systemd/user.
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

python_has_pyusb() { # <python>
    "$1" -c 'import usb' >/dev/null 2>&1
}

PYUSB_HINT="install pyusb for the host python3 (Fedora: sudo dnf install python3-pyusb; otherwise: python3 -m pip install --user pyusb), or use --runner distrobox"

container=dev
python_path=""
reason=""
case "$runner" in
auto)
    python_path=$(command -v python3 || true)
    if [[ -n "$python_path" ]] && python_has_pyusb "$python_path"; then
        runner=host
        reason="$python_path can import pyusb"
    elif command -v distrobox-enter >/dev/null 2>&1; then
        runner=distrobox
        reason="the host python3 has no pyusb and distrobox-enter exists"
    else
        die "no way to run the watcher: the host python3 has no pyusb and distrobox-enter was not found. $PYUSB_HINT"
    fi
    ;;
host)
    python_path=$(command -v python3 || true)
    [[ -n "$python_path" ]] || die "python3 was not found on PATH"
    python_has_pyusb "$python_path" || die "$python_path cannot import pyusb: $PYUSB_HINT"
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

check_path "the script folder" "$script_dir"
check_path "the driver folder" "$driver_dir"
for name in eject-watch-run.sh eject-watch-stop.sh; do
    [[ -x "$script_dir/$name" ]] || die "$script_dir/$name is missing or not executable"
done

pythonpath=$driver_dir
if [[ "$runner" == host ]]; then
    check_plain "the python3 path" "$python_path"
    [[ -x "$python_path" ]] || die "the python3 path is not executable: '$python_path'"
    template=$script_dir/opticfilm-eject-watch-host.service
    if [[ -n "${OF135I_DEPS_OVERLAY:-}" ]]; then
        check_path "OF135I_DEPS_OVERLAY" "${OF135I_DEPS_OVERLAY%/}"
        pythonpath=$driver_dir:${OF135I_DEPS_OVERLAY%/}
    fi
    echo "runner: host ($reason)"
else
    template=$script_dir/opticfilm-eject-watch.service
    echo "runner: distrobox:$container ($reason)"
fi
[[ -f "$template" ]] || die "unit template not found: $template"

unit_dir=${EJECT_WATCH_UNIT_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user}
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
    systemctl --user daemon-reload
    echo "ran: systemctl --user daemon-reload"
else
    echo "systemctl not found; run 'systemctl --user daemon-reload' yourself" >&2
fi
echo "not enabled or started. To do that: systemctl --user enable --now opticfilm-eject-watch.service"
