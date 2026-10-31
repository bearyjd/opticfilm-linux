# Contributing

- **Tests first.** Write the failing test, watch it fail for the right reason, then make it pass.
- **Coverage gate:** 80 % (`uv run pytest --cov=opticfilm_linux --cov-fail-under=80`), plus
  `uv run ruff check .` and `uv run ruff format --check .`. CI runs both.
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
  `QT_QPA_PLATFORM=offscreen .venv-qt/bin/python -m pytest -m qt tests/qt`. Qt tests carry
  `@pytest.mark.qt`, are excluded from the default run, and run in the optional Fedora CI job.

<!-- AUTO-GENERATED from pyproject.toml, the CLI definitions and the environment lookups in src/ (2026-10-29). Edit the source, not this section. -->
## Commands

| Command | What it does |
|---------|--------------|
| `ofscan devices` | List supported scanners (read-only) |
| `ofscan scan [--fake DIR] [--dpi N]` | Scan a holder and process it (default 3600 dpi) |
| `ofscan process SWEEP_DIR` | Process a stored sweep |
| `ofscan enhance FILE... [--dpi N]` | Enhance finished images from other scanning software |
| `ofscan batch new \| status \| scan \| eject \| load \| confirm-scanner \| accept-all \| export` | Drive a batch from a terminal; `load` needs `OPTICFILM_NEXT_STRIP=1` |
| `opticfilm [--fake SWEEP_DIR]` | The desktop app (needs the distribution's PySide6 and Kirigami) |
| `python scripts/check_spike.py DIR` | Offline verdict for a hardware-run folder |
| `python scripts/poll_report.py DIR...` | Offline poll-timeout report for run folders |
| `python scripts/rehearse.py --dpi N [--work DIR] [--keep]` | Rehearse one tray with the fake driver and measure what it costs |
| `scripts/install-eject-watch.sh [--runner auto\|host\|distrobox[:NAME]] [DRIVER_DIR]` | Render the Eject-watcher systemd user unit (host python3 or distrobox); never enables or starts it |
| `scripts/opticfilm-here` | Start the app from this checkout on the real scanner (dev container) |
| `scripts/eject-watch-check.sh [--live] [--enable] [--runner R] [DRIVER_DIR]` | One-command check of the Eject-watcher unit: scanner OFF proves the start path and a clean stop; `--live` (scanner ON) proves the live stop; `--runner host\|distrobox[:NAME]` picks the container-free or distrobox unit (default auto); `--enable` enables it after a PASS |

Gates: `pytest` (default run excludes the `hardware` and `qt` markers), coverage at least 80 %,
`ruff check` and `ruff format --check` (line length 100, Python 3.11 target).

## Environment variables

| Variable | Required | Meaning |
|----------|----------|---------|
| `OPTICFILM_DRIVER_DIR` | For the real scanner, unless `of135i` is importable | Checkout of opticfilm135i-linux holding the `of135i/` package. Without it and without an importable `of135i`, the scanner is reported as not found |
| `OPTICFILM_DRIVER_RUNNER` | No | How the shown load command runs the driver: unset = distrobox container `dev`, `host` = directly, `distrobox:<name>` = that container (letters, digits, `-`, `_`, `.`; must start with a letter or digit). Anything else falls back to the default and is logged |
| `OPTICFILM_NEXT_STRIP` | No (off) | `1` enables the hardware-unproven `load --next-strip` and the app's load-next-holder; any other value leaves it off |
| `OPTICFILM_EJECT_BUTTON` | No (on) | `0`, `false`, `no` or `off` (any case) switches the front-panel Eject button off; anything else leaves it on |
| `OPTICFILM_LOAD_COMMAND` | No | Replaces the first-load terminal command shown in the app's help dialog |
| `XDG_STATE_HOME` | No | Driver logs go to `$XDG_STATE_HOME/opticfilm-linux/logs` (default `~/.local/state`) |
| `XDG_CACHE_HOME` | No | Scan scratch goes to `$XDG_CACHE_HOME/opticfilm-linux/work` (default `~/.cache`; keep it off a RAM-backed `/tmp`) |
<!-- /AUTO-GENERATED -->
