#!/bin/bash
# Installs the Eject-button watcher unit for the current user. It renders the unit template with
# this checkout's script folder and your driver folder, writes it to ~/.config/systemd/user and
# runs `systemctl --user daemon-reload`. It does NOT enable or start the unit.
#
# Usage: install-eject-watch.sh [driver_dir]     (default: $OPTICFILM_DRIVER_DIR)
#
# Both folders end up in the unit as bare words that distrobox-enter evals, so they must be
# absolute and made only of letters, digits and _ . / - (no spaces, %, $, quotes or the like).
# Set EJECT_WATCH_UNIT_DIR to write somewhere other than ~/.config/systemd/user.
set -euo pipefail

die() {
    echo "install-eject-watch: $*" >&2
    exit 1
}

check_path() { # <what> <path>
    local what=$1 path=$2
    [[ -n "$path" ]] || die "$what is empty"
    # LC_ALL=C: in a UTF-8 locale a range like A-Z can also match letters such as e-acute.
    LC_ALL=C bash -c '[[ $1 =~ ^[A-Za-z0-9_./-]+$ ]]' _ "$path" ||
        die "$what has characters that are not allowed in the unit (only letters, digits and _ . / - are): '$path'"
    [[ "$path" == /* ]] || die "$what must be an absolute path: '$path'"
    [[ -d "$path" ]] || die "$what is not a folder: '$path'"
}

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
script_dir=$(cd -- "$here/../packaging/systemd" && pwd)
template=$script_dir/opticfilm-eject-watch.service
driver_dir=${1:-${OPTICFILM_DRIVER_DIR:-}}
[[ -n "$driver_dir" ]] || die "give the driver folder as the first argument or set OPTICFILM_DRIVER_DIR"
driver_dir=${driver_dir%/}

check_path "the script folder" "$script_dir"
check_path "the driver folder" "$driver_dir"
for name in eject-watch-run.sh eject-watch-stop.sh; do
    [[ -x "$script_dir/$name" ]] || die "$script_dir/$name is missing or not executable"
done
[[ -f "$template" ]] || die "unit template not found: $template"

unit_dir=${EJECT_WATCH_UNIT_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user}
unit=$unit_dir/opticfilm-eject-watch.service

content=$(<"$template")
content=${content//@SCRIPT_DIR@/$script_dir}
content=${content//@DRIVER_DIR@/$driver_dir}
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
