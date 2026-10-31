# Hardware test scripts

Helpers for supervised runs against a real OpticFilm 135i. They are not run by the test suite.

| Script | What it does |
|---|---|
| `hw_run.sh <attempt_dir> <name> <of135i args...>` | Runs one driver command with a per-run trace (`OF135I_TRACE`), calibration dump (`OF135I_DUMP_CAL=1`) and a log under `spike/logs`. Passes the driver's exit status through. After a failed run it records the scanner state with the read-only `doctor` command. |
| `ab_gate_test.sh <attempt_dir>` | Interleaved A/B of the calibration stop gate: a discarded warm-up plus six slide-4 scans at 600 dpi, order balanced (off/on). Stops at the first failed scan and prints a per-scan summary. |
| `tray_and_armc_test.sh <tray\|armc> <attempt_dir>` | `tray`: one four-slide scan at 3600 dpi. `armc`: four slide-4 scans at 600 dpi comparing the buffer-empty wait with the stop gate off against the stop gate on. |
| `merge_when_green.sh <owner/repo> <pr> <branch>` | Optional maintainer tooling, not needed to use the scanner. Queues (flock), updates the PR branch, waits for CI green on the current head, merges with a merge commit. Never deletes branches. Needs `gh`. |

## Environment

- `OPTICFILM_DRIVER_DIR` (required): the driver checkout; `$OPTICFILM_DRIVER_DIR/of135i` must exist.
- `OF135I_DEPS_OVERLAY` (optional): an extra `PYTHONPATH` entry, for example a folder holding
  pure-Python dependencies such as `tifffile`.
- `PYTHON` (optional): interpreter, default `python3`. It must have `pyusb`.
- `DOCTOR_CMD` (optional): replaces the failure-capture command. `HW_RUN_NO_DOCTOR=1` turns the capture off.

With a distrobox container: `distrobox enter <name> -- bash -lc 'cd <repo> && scripts/hardware/<script> ...'`.

## Rules these scripts rely on

- Supervised only: the owner watches the scanner the whole time.
- Anomaly rule: if the motor grinds or stalls, power the scanner OFF.
- Run `load` in a real terminal, never through an agent's or IDE's `!` prefix.
- One `load` and one power-on per series of runs.
- Run in an environment that has `pyusb`.
- Failure capture: after a failed run the scripts run the read-only `doctor` (30 s timeout, never
  changes the exit status) so the scanner's state is recorded BEFORE anyone power-cycles it;
  a power cycle may lose it.
- The eject-watch service holds the scanner; stop it before running driver commands from a
  terminal (see `docs/eject-watch-service.md`).
