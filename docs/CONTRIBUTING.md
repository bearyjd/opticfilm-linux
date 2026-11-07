# Contributing

- **Tests first.** Write the failing test, watch it fail for the right reason, then make it pass.
- **Coverage gate:** 80 % (`uv run pytest --cov=opticfilm_linux --cov-fail-under=80`), plus
  `uv run ruff check .` (CI runs the lint and the coverage run; run `uv run ruff format --check .`
  yourself before pushing, CI does not).
- **Immutable data flow.** Pipeline steps return new arrays and never modify their input;
  dataclasses are frozen.
- **Hardware tests** carry `@pytest.mark.hardware`, are read-only, and are excluded from the
  default run and from CI. Nothing in this repository may send commands to the scanner without an
  explicit, reviewed design.
- **No vendor material.** Do not commit Plustek code, binaries, firmware, ICC profiles,
  configuration values, documentation or USB captures.
- Small files (under 800 lines), small functions (under 50 lines), conventional commit messages
  (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- **The desktop app** (`opticfilm`): all behaviour lives in the Qt-free controller
  (`opticfilm_linux.app`, tested in the normal run); `opticfilm_linux.app.qt` and the QML pages are
  thin. The Qt layer needs the distribution's PySide6 and Kirigami (on Fedora:
  `dnf install python3-pyside6 kf6-kirigami qt6-qtdeclarative`). Use a separate environment that
  can see them: `uv venv --system-site-packages --python /usr/bin/python3 .venv-qt &&
  UV_PROJECT_ENVIRONMENT=.venv-qt uv sync --python /usr/bin/python3`, then
  `QT_QPA_PLATFORM=offscreen .venv-qt/bin/python -m pytest -m qt tests/qt -q`. Qt tests carry
  `@pytest.mark.qt` (the opt-in responsive layout matrix carries `qt_matrix`); both are excluded
  from the default run, and the `qt` tests run in the optional Fedora CI job.

<!-- AUTO-GENERATED from pyproject.toml, the CLI definitions, the scripts/ folder and the environment lookups in src/ and scripts/ (2026-10-31). Edit the source, not this section. -->
## Commands

| Command | What it does |
|---------|--------------|
| `ofscan` / `opticfilm` | Installed entry points: `opticfilm_linux.cli:main` / `opticfilm_linux.app.qt.app:main` (`pyproject.toml` `[project.scripts]`) |
| `ofscan devices` | List supported scanners (read-only) |
| `ofscan scan [--fake DIR] [--dpi N]` | Scan a holder and process it (default 3600 dpi) |
| `ofscan process SWEEP_DIR` | Process a stored sweep |
| `ofscan enhance FILE... [--dpi N]` | Enhance finished images from other scanning software |
| `ofscan batch new \| status \| scan \| eject \| load \| confirm-scanner \| accept-all \| export` | Drive a batch from a terminal; `load` is enabled by default; `OPTICFILM_NEXT_STRIP=0` disables it |
| `opticfilm [--fake SWEEP_DIR]` | The desktop app (needs the distribution's PySide6 and Kirigami) |
| `python scripts/check_spike.py DIR` | Offline verdict for a hardware-run folder |
| `python scripts/poll_report.py DIR...` | Offline poll-timeout report for run folders |
| `python scripts/rehearse.py --dpi N [--work DIR] [--keep]` | Rehearse one tray with the fake driver and measure what it costs |
| `scripts/install-eject-watch.sh [--runner auto\|host\|distrobox[:NAME]] [DRIVER_DIR]` | Render the Eject-watcher systemd user unit (host python3 or distrobox); never enables or starts it |
| `scripts/opticfilm-here` | Start the app from this checkout on the real scanner (dev container) |
| `scripts/eject-watch-check.sh [--live] [--enable] [--runner R] [DRIVER_DIR]` | One-command check of the Eject-watcher unit: scanner OFF proves the start path and a clean stop; `--live` (scanner ON) proves the live stop; `--runner host\|distrobox[:NAME]` picks the container-free or distrobox unit (default auto); `--enable` enables it after a PASS |
| `scripts/hardware/hw_run.sh ATTEMPT_DIR NAME of135i-ARGS...` | Run one driver command with a trace and log; after a failure it records the scanner state with the read-only `doctor` (see `scripts/hardware/README.md`) |
| `scripts/hardware/ab_gate_test.sh ATTEMPT_DIR` / `tray_and_armc_test.sh tray\|armc ATTEMPT_DIR` | Supervised hardware series that move the motor; they need a real terminal for the confirmation |
| `scripts/hardware/merge_when_green.sh OWNER/REPO PR BRANCH` | Maintainer tooling: wait for CI on the current head, then merge with a merge commit |
| `uv sync` | Install project dependencies and the default `dev` group: `pytest>=8.0`, `pytest-cov>=5.0`, `ruff>=0.5` (`pyproject.toml`); also used by CI |
| `uv run pytest --cov=opticfilm_linux --cov-fail-under=80` | Normal CI test command; default pytest selection is `-m 'not hardware and not qt and not qt_matrix'`. `hardware` means read-only real-scanner tests; `qt` means distribution PySide6/Kirigami headless smoke tests (`pyproject.toml`, `.github/workflows/ci.yml`) |
| `uv run ruff check .` | Lint command run by normal CI (`.github/workflows/ci.yml`); rules `E F I B UP T20` |
| `uv run ruff format --check .` | Format check; not run by CI, run it locally (line length 100) |
| `uv run pytest tests/test_app_scan_openings.py::test_name -q` | Run a single test (any `file::test` id, or `-k EXPR`) |
| `uv run pytest -m qt_matrix tests/qt` | Opt-in responsive layout matrix; needs the `.venv-qt` environment like the `qt` tests, not part of CI |
| `QT_QPA_PLATFORM=offscreen .venv-qt/bin/python -m pytest -m qt tests/qt -q` | Optional Fedora CI Qt job; `-m qt` overrides the default marker selection. CI creates `.venv-qt` with `--system-site-packages` using `/usr/bin/python3`, then syncs with `UV_PROJECT_ENVIRONMENT=.venv-qt` (`.github/workflows/ci.yml`) |

Gates: `pytest` (default run excludes the `hardware`, `qt` and `qt_matrix` markers), coverage at
least 80 % (the Qt layer `opticfilm_linux/app/qt/*` is omitted from coverage), `ruff check` in CI
and `ruff format --check` locally (line length 100, Python 3.11 target, `requires-python >=3.11`).
Source rules enforced by tests: functions under 50 lines (`tests/test_pr2b_function_lengths.py`,
`tests/test_review_function_lengths.py`; the latter compares against git history, which is why CI
checks out full history). Files stay under 800 lines by convention only.

PR checklist: failing test first; `uv run ruff check .`; `uv run ruff format --check .`;
`uv run pytest --cov=opticfilm_linux --cov-fail-under=80`; conventional commit message; no vendor
material; no scanner command without a reviewed design; update `docs/ENV.md` when a variable
changes.

## Environment variables

| Variable | Required | Meaning |
|----------|----------|---------|
| `OPTICFILM_DRIVER_DIR` | For the real scanner, unless `of135i` is importable | Checkout of opticfilm135i-linux holding the `of135i/` package. Without it and without an importable `of135i`, the scanner is reported as not found |
| `OPTICFILM_DRIVER_RUNNER` | No | How the shown load command runs the driver: unset = distrobox container `dev`, `host` = directly, `distrobox:<name>` = that container (letters, digits, `-`, `_`, `.`; must start with a letter or digit). Anything else falls back to the default and is logged |
| `OPTICFILM_NEXT_STRIP` | No (on) | Exact case-insensitive `0`, `false`, `no`, or `off` disables `load --next-strip` and the app's load-next-holder; unset, empty, and anything else enables it. First load after power-on still uses the interactive terminal command |
| `OPTICFILM_EJECT_BUTTON` | No (on) | `0`, `false`, `no` or `off` (any case) switches the front-panel Eject button off; anything else leaves it on |
| `OPTICFILM_DRIVER_CLEAN` | No (off) | `1` lets the driver run its own IR dust clean again; otherwise scans pass `--no-clean` and only the app's dust repair runs |
| `OPTICFILM_SKIP_EMPTY` | No (off) | `1`, `true`, `yes` or `on` (any case) shows "Slides in this holder" next to Eject: N scans openings 1..N and records the rest as empty, not scanned. The choice goes back to 4 when Scan is pressed (even if the scan is refused or fails), on any holder move or latch change (eject, load next, first load, latch confirmed), and when a batch is opened, closed or created; it cannot be changed while a scan is queued or running. A rescan always scans all four openings: it restores the reserved numbers and drives the motor to openings the owner had marked empty. Untested on hardware |
| `OPTICFILM_SKIP_GAPS` | No (off) | With `OPTICFILM_SKIP_EMPTY` on: four opening toggles, so any ascending set (for example `1,3,4`) can be scanned. Alone it does nothing. Untested on hardware |
| `OPTICFILM_AUTO_EJECT` | No (off) | `1`, `true`, `yes` or `on` (any case): after a good tray (sweep saved, tray SCANNED, scan ended) the app runs one separate `eject` through the usual Eject path, never `scan --eject`; no eject after any failed, stopped or cut-off scan, and a refused one shows "Automatic eject skipped". No automatic load. Fake driver: `FAKE_DRIVER_EJECT_MODE` = `ok`, `skipped`, `fail` or `hang`. Untested on hardware (plan rungs H5, H7, H8) |
| `OPTICFILM_LOAD_AND_SCAN` | No (off) | `1`/`0` (same values as the other switches) decides over the saved Load & scan setting and locks it; on: "Holder latched – Load & scan" (or Enter) after an app eject loads once, waits for a fresh READY (30 s) and scans; never retried, never started by auto-eject or the front-panel button. Untested on hardware |
| `OPTICFILM_WINDOW_SIZE` | No | `WIDTHxHEIGHT` (for example `1400x900`) overrides the app's startup window size; malformed values are ignored |
| `OPTICFILM_LOAD_COMMAND` | No | Replaces the first-load terminal command shown in the app's help dialog |
| `OPTICFILM_HW_ASSUME_YES` | No | Only for `scripts/hardware/` wrappers that supervise the scanner: skips the interactive confirmation of the motor-moving series and prints a notice. Without it, a closed or piped stdin aborts with no driver call |
| `OF135I_DEPS_OVERLAY` | No | Extra `PYTHONPATH` entry for `scripts/hardware/hw_run.sh` (a folder with pure-Python driver deps such as `tifffile`) |
| `PYTHON` | No (`python3`) | Interpreter used by `scripts/hardware/hw_run.sh` |
| `HW_RUN_LOG_DIR` | No (`spike/logs`) | Where `hw_run.sh` writes its logs |
| `HW_RUN_NO_DOCTOR` / `DOCTOR_CMD` | No | `HW_RUN_NO_DOCTOR=1` turns off the read-only state capture after a failed run; `DOCTOR_CMD` overrides the command (trusted, owner-set; split on whitespace, never evaluated) |
| `XDG_STATE_HOME` | No | Driver logs go to `$XDG_STATE_HOME/opticfilm-linux/logs` (default `~/.local/state`) |
| `XDG_CACHE_HOME` | No | Scan scratch goes to `$XDG_CACHE_HOME/opticfilm-linux/work` (default `~/.cache`; keep it off a RAM-backed `/tmp`) |
<!-- /AUTO-GENERATED -->
