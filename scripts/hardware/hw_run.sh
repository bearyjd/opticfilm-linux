#!/bin/bash
# usage: hw_run.sh <attempt_dir> <name> <of135i args...>
# Runs one supervised driver command with a per-run trace and a log under spike/logs.
# Env: OPTICFILM_DRIVER_DIR  driver checkout that contains the of135i package (required)
#      OF135I_DEPS_OVERLAY   optional extra PYTHONPATH entry (a folder with pure-Python deps, e.g. tifffile)
#      PYTHON                interpreter, default python3 (must have pyusb)
#      DOCTOR_CMD            override the failure capture command (trusted, owner-set; split on whitespace,
#                            never eval'd); HW_RUN_NO_DOCTOR=1 disables it
#      HW_RUN_LOG_DIR        log directory, default spike/logs (relative to the repo root)
set -o pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [ -z "${OPTICFILM_DRIVER_DIR:-}" ]; then
  echo "hw_run.sh: OPTICFILM_DRIVER_DIR is not set (point it at the driver checkout that contains of135i/)" >&2
  exit 2
fi
if [ ! -e "$OPTICFILM_DRIVER_DIR/of135i" ]; then
  echo "hw_run.sh: $OPTICFILM_DRIVER_DIR/of135i does not exist (OPTICFILM_DRIVER_DIR must contain the of135i package)" >&2
  exit 2
fi
OPTICFILM_DRIVER_DIR="$(cd "$OPTICFILM_DRIVER_DIR" && pwd)"
if [ -n "${OF135I_DEPS_OVERLAY:-}" ]; then OF135I_DEPS_OVERLAY="$(cd "$OF135I_DEPS_OVERLAY" && pwd)"; fi
PY="${PYTHON:-python3}"
attempt=${1:?usage: hw_run.sh <attempt_dir> <name> <of135i args...>}
name=${2:?usage: hw_run.sh <attempt_dir> <name> <of135i args...>}
shift 2
cd "$ROOT" || exit 2
export PYTHONPATH="$OPTICFILM_DRIVER_DIR${OF135I_DEPS_OVERLAY:+:$OF135I_DEPS_OVERLAY}"
export OF135I_DUMP_CAL=1
log_dir="${HW_RUN_LOG_DIR:-spike/logs}"
mkdir -p "$log_dir" "$attempt"
log="$log_dir/$(basename "$attempt")-$name.log"; start=$SECONDS
OF135I_TRACE="$attempt/trace-$name.jsonl" "$PY" -u -m of135i "$@" 2>&1 | tee -a "$log"
codes=("${PIPESTATUS[@]}")
printf 'driver_exit=%s tee_exit=%s elapsed=%ss\n' "${codes[0]}" "${codes[1]}" "$((SECONDS-start))" | tee -a "$log"
# After a failed run, record the scanner's state BEFORE anyone power-cycles it (read-only: `doctor` opens the
# device readonly -- no set_configuration, no driver detach, no reset, no register writes). Never blocks or
# changes the exit status. DOCTOR_CMD can be overridden (tests use `echo`); HW_RUN_NO_DOCTOR=1 disables it.
if (( codes[0] != 0 )) && [ -z "${HW_RUN_NO_DOCTOR:-}" ]; then
  echo "--- read-only state capture after failure (doctor) ---" | tee -a "$log"
  if [ -n "${DOCTOR_CMD:-}" ]; then read -r -a doctor <<<"$DOCTOR_CMD"
  else doctor=("$PY" -u -m of135i doctor --json "$attempt/doctor-after-$name.json"); fi
  timeout 30 "${doctor[@]}" 2>&1 | tee -a "$log" || true
fi
(( codes[0] == 0 && codes[1] == 0 ))
