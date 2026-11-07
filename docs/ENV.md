# Environment variables

<!-- AUTO-GENERATED -->
Generated from environment reads and assignments in `src/`, `scripts/`, and `packaging/`. Source references are repository-relative `file:line`. Optional means the caller may omit the variable; it does not imply that every supplied value is validated. External driver and Qt behavior beyond these reads is outside this inventory.

## App runtime

| Variable | Required? | Purpose; accepted values and default | Source |
| --- | --- | --- | --- |
| `OPTICFILM_DRIVER_RUNNER` | Optional | Chooses the runner for the first-load **help command**. Whitespace is stripped. `host` runs directly; `distrobox:NAME` accepts a name matching `[A-Za-z0-9][A-Za-z0-9_.-]*`. Unset, empty, or invalid values use container `dev`; invalid nonempty values are shown in the help note and logged when the command is generated. This does not select the scanner subprocess runner. | `src/opticfilm_linux/app/messages.py:125` |
| `OPTICFILM_LOAD_COMMAND` | Optional | Nonempty stripped text replaces the first-load command shown in help; empty/unset generates the normal command. | `src/opticfilm_linux/app/qt/app.py:79`, `src/opticfilm_linux/app/qt/app.py:90` |
| `OPTICFILM_WINDOW_SIZE` | Optional | `WIDTHxHEIGHT` in positive integers (for example `1400x900`, case-insensitive `x`). Overrides the saved window size at startup, clamped to the screen's work area. Malformed, zero or negative values are ignored (default geometry), so a typo cannot prevent startup. An explicit size passed in code wins over it. Not hardware-facing. | `src/opticfilm_linux/app/qt/app.py:134`; `src/opticfilm_linux/app/qt/window_geometry.py:83` |
| `OPTICFILM_NEXT_STRIP` | Optional | `load --next-strip` is on by default. `0`, `false`, `no`, or `off` (case-insensitive exact tokens) disables it; unset, empty, and all other values enable it. First load after power-on still needs the interactive terminal command. | `src/opticfilm_linux/scanner/select.py:49` |
| `OPTICFILM_DRIVER_CLEAN` | Optional | `1` (exact) lets the driver run its own IR dust clean again (omits `--no-clean`); unset or anything else passes `--no-clean`, so the app's own dust repair works on the unfilled frames. | `src/opticfilm_linux/scanner/driver.py:487` |
| `OPTICFILM_EJECT_BUTTON` | Optional | Front-panel Eject support is enabled by default. Stripped, case-insensitive `0`, `false`, `no`, or `off` disables it; all other values leave it enabled. | `src/opticfilm_linux/scanner/select.py:54` |
| `OPTICFILM_SKIP_EMPTY` | Optional | Off by default. Stripped, case-insensitive `1`, `true`, `yes`, or `on` shows "Slides in this holder: 4 3 2 1" next to Eject; choosing N scans openings 1..N (`--slides 1-N`) and records the rest as empty, not scanned, with their numbers kept free. The choice goes back to 4 when Scan is pressed (even if the scan is refused or fails), on any holder move or latch change (eject, load next, first load, latch confirmed), and when a batch is opened, closed or created; it cannot be changed while a scan is queued or running. A rescan always scans all four openings: it restores the reserved numbers and drives the motor to openings the owner had marked empty. Unset or blank: the saved "Choose which holder openings to scan" workflow setting decides (off on a fresh profile); an unreadable value is ignored with a logged warning and also defers to it. Set either way, the variable wins and locks the switch in Workflow settings. Off: no selector and today's `--slides 1-4` command. Untested on hardware. | `src/opticfilm_linux/scanner/select.py:30`, `src/opticfilm_linux/scanner/select.py:34` |
| `OPTICFILM_SKIP_GAPS` | Optional | Off by default; same accepted values as `OPTICFILM_SKIP_EMPTY` and does nothing without it. On: four opening toggles instead, so any ascending set can be scanned (for example `--slides 1,3,4`). Non-consecutive lists are untested on hardware. | `src/opticfilm_linux/scanner/select.py:30`, `src/opticfilm_linux/scanner/select.py:39` |
| `OPTICFILM_AUTO_EJECT` | Optional | **Hardware-facing, risky:** moves the motor without a click. Off by default; same accepted values and same override rule as `OPTICFILM_SKIP_EMPTY` (the saved "Eject the holder after each good scan" setting is used when unset), read once when the app starts. On: after a good tray, the app runs its own separate `eject` (never the driver's `scan --eject`), once the sweep is saved, the tray reached "Waiting to enhance" and the scan has fully ended; enhancement and automatic export carry on meanwhile. It goes through the usual Eject path (interrupted mark, holder latch, power-cycle banner if it fails; the tray stays good). A failed, stopped, interrupted or cut-off scan, a closed batch, a set interrupted mark, a busy holder, or a driver that exited with an error although every frame was written (that tray is kept) means no eject; a refused one says "Automatic eject skipped" and is not retried, and an eject the driver did not confirm says "Eject did not run, check the slot". A rescan ejects the same way (the owner is to confirm this is wanted). The motor starts without a click: keep hands clear of the slot, especially during rung H5. Known limits: Confirm holder latched does nothing while an automatic export runs (as before; Load next holder still works), and a batch replaced in the instant between the scan ending and the eject starting is an accepted narrow window. Nothing loads by itself: press Load next holder (or confirm the holder is latched) before the next scan. Untested on hardware: waits for rungs H5, H7 and H8 of the empty-slots plan. | `src/opticfilm_linux/scanner/select.py:30`, `src/opticfilm_linux/scanner/select.py:44` |
| `OPTICFILM_LOAD_AND_SCAN` | Optional | **Hardware-facing:** off by default; same accepted values as `OPTICFILM_SKIP_EMPTY`, read once when the app starts. Set either way, it decides over the saved "Load & scan with one press" workflow setting and locks that switch; a set value that is not an on/off value (e.g. `maybe`) counts as OFF and also locks the switch (logged), unlike the other switches; unset or blank, the saved setting (off on a fresh profile) is used. On: after an eject this app saw finish, the "Holder latched – Load & scan" button (or Enter) loads the next holder once, waits up to 30 s for a fresh READY, then scans it; the press is the owner's "seated and latched" confirmation. Off: the button is hidden and Enter does nothing. Untested on hardware. | `src/opticfilm_linux/scanner/select.py`, `src/opticfilm_linux/app/load_and_scan.py` |

## Driver and scanner

| Variable | Required? | Purpose; accepted values and default | Source |
| --- | --- | --- | --- |
| `OPTICFILM_DRIVER_DIR` | Optional in app | Driver checkout containing an `of135i` directory. Empty/unset uses an importable `of135i` on the Python path. Also supplies the generated help command's directory. The Flatpak manifest sets `/app/share/opticfilm135i-linux` (`packaging/flatpak/io.github.bearyjd.OpticFilmLinux.json:28`). `scripts/opticfilm-here` exports a default of `<repo>/.local-research/ref/opticfilm135i-linux` when empty/unset. | `src/opticfilm_linux/scanner/driver.py:91`; `src/opticfilm_linux/app/qt/app.py:84`; `scripts/opticfilm-here:7` |
| `XDG_STATE_HOME` | Optional | Driver logs use `<value>/opticfilm-linux/logs`, defaulting to `~/.local/state/opticfilm-linux/logs`. Interrupted-scan state uses `<value>/opticfilm-linux` only for a nonempty absolute value; otherwise it uses `~/.local/state/opticfilm-linux`. The driver log configuration does not apply that absolute-path check. | `src/opticfilm_linux/scanner/driver.py:92`; `src/opticfilm_linux/scanner/interrupted.py:43` |
| `XDG_CACHE_HOME` | Optional | Scan scratch directory is `<value>/opticfilm-linux/work`; default `~/.cache/opticfilm-linux/work`. Used both by environment configuration and the scratch fallback. No path validation at these reads. | `src/opticfilm_linux/scanner/driver.py:93`, `src/opticfilm_linux/scanner/driver.py:118` |
| `PYTHONPATH` | Optional | Existing value is inherited by driver subprocesses; when a driver directory is configured, that directory plus a path separator is prepended. Other environment entries are copied unchanged. | `src/opticfilm_linux/scanner/driver.py:604`, `src/opticfilm_linux/scanner/driver.py:606`, `src/opticfilm_linux/scanner/driver.py:743` |

## Hardware scripts

These scripts can operate the scanner. This inventory was produced without running them.

| Variable | Required? | Purpose; accepted values and default | Source |
| --- | --- | --- | --- |
| `OPTICFILM_DRIVER_DIR` | Required for `hw_run.sh` | Nonempty checkout path containing `of135i`; normalized through `cd`/`pwd`. | `scripts/hardware/hw_run.sh:12` |
| `OF135I_DEPS_OVERLAY` | Optional | Nonempty directory normalized through `cd`/`pwd` and appended to the constructed `PYTHONPATH`. Default: no overlay. | `scripts/hardware/hw_run.sh:21`, `scripts/hardware/hw_run.sh:27` |
| `PYTHON` | Optional | Executable used for hardware driver commands and report parsing. Empty/unset defaults to `python3`; quoted as one executable, not split into command arguments. | `scripts/hardware/hw_run.sh:22`; `scripts/hardware/ab_gate_test.sh:34`; `scripts/hardware/tray_and_armc_test.sh:47` |
| `HW_RUN_LOG_DIR` | Optional | Log directory; empty/unset defaults to `spike/logs` relative to repository root. | `scripts/hardware/hw_run.sh:26`, `scripts/hardware/hw_run.sh:29` |
| `HW_RUN_NO_DOCTOR` | Optional | Any nonempty value skips the post-failure doctor capture; empty/unset permits it. | `scripts/hardware/hw_run.sh:38` |
| `DOCTOR_CMD` | Optional | Nonempty text is split by Bash `read -r -a` into doctor command arguments. No shell evaluation; see the script for splitting behavior. Empty/unset uses the selected Python with `-u -m of135i doctor --json <attempt>/doctor-after-<name>.json`. Capture has a 30-second timeout. | `scripts/hardware/hw_run.sh:40` |
| `OPTICFILM_HW_ASSUME_YES` | Optional | **Hardware-facing, risky:** any nonempty value (even `0`) bypasses interactive confirmation in both hardware experiment scripts, intended for supervised wrappers; empty/unset requires terminal confirmation. | `scripts/hardware/ab_gate_test.sh:19`; `scripts/hardware/tray_and_armc_test.sh:19` |
| `XDG_RUNTIME_DIR`, `TMPDIR` | Optional | Merge-queue lock directory: first nonempty `XDG_RUNTIME_DIR`, then `TMPDIR`, then `/tmp`; filename `opticfilm-merge-queue.lock`. | `scripts/hardware/merge_when_green.sh:8` |

## Eject-watch installer and check

| Variable | Required? | Purpose; accepted values and default | Source |
| --- | --- | --- | --- |
| `OPTICFILM_DRIVER_DIR` | Required if directory argument omitted | Fallback driver directory for installer and check. Installer requires an absolute path containing only letters, digits, `_`, `.`, `/`, `-`, checks the resolved path too, and removes a trailing slash. | `scripts/install-eject-watch.sh:28`, `scripts/install-eject-watch.sh:63`; `scripts/eject-watch-check.sh:447` |
| `OF135I_DEPS_OVERLAY` | Optional | Host-runner dependency directory appended to installer-generated `PYTHONPATH`; same installer path restrictions apply. Not included in the distrobox runner's Python path. | `scripts/install-eject-watch.sh:79`, `scripts/install-eject-watch.sh:137` |
| `XDG_CONFIG_HOME`, `HOME` | Optional / fallback | Unit directory is `<XDG_CONFIG_HOME>/systemd/user`; empty/unset XDG setting uses `$HOME/.config/systemd/user`. The scripts use `set -u`, so `HOME` must exist when that fallback is evaluated. | `scripts/install-eject-watch.sh:142`; `scripts/eject-watch-check.sh:488` |
| `EJECT_WATCH_STOP_LOOPS` | Optional | Container stop helper polling count, empty/unset `100`; consumed as Bash arithmetic without explicit validation. | `packaging/systemd/eject-watch-stop.sh:14`, `packaging/systemd/eject-watch-stop.sh:19` |
| `EJECT_WATCH_STOP_DELAY` | Optional | Delay passed to `sleep` between stop polls, empty/unset `1` second; no explicit validation. | `packaging/systemd/eject-watch-stop.sh:15`, `packaging/systemd/eject-watch-stop.sh:21` |
| `EJECT_WATCH_CHECK_TESTING` | Optional | Exactly `1` enables the test overrides below. Otherwise the check ignores nonempty override values with a note; installer ignores its unit-directory override and uses real `/proc`. Default: disabled. | `scripts/eject-watch-check.sh:24`; `scripts/install-eject-watch.sh:143`, `scripts/install-eject-watch.sh:175` |

### Test-only overrides

All are optional and require `EJECT_WATCH_CHECK_TESTING=1`. Empty values select the defaults below. Paths, commands, and timing values have no explicit validation at these assignments; timing counts are used in shell arithmetic or passed to `sleep`/`timeout`, as shown in the source.

| Variable | Purpose | Default | Source |
| --- | --- | --- | --- |
| `SYSTEMCTL` | Check's systemctl executable | `systemctl` | `scripts/eject-watch-check.sh:32`, `scripts/eject-watch-check.sh:114` |
| `JOURNALCTL` | Check's journal executable | `journalctl` | `scripts/eject-watch-check.sh:33`, `scripts/eject-watch-check.sh:134` |
| `PROC_ROOT` | Process inspection root; also installer inspection root in test mode | `/proc` | `scripts/eject-watch-check.sh:34`, `scripts/eject-watch-check.sh:171`; `scripts/install-eject-watch.sh:176` |
| `SYSFS_USB_ROOT` | USB presence inspection root | `/sys/bus/usb/devices` | `scripts/eject-watch-check.sh:35`, `scripts/eject-watch-check.sh:251` |
| `CGROUP_ROOT` | Root prefixed to the unit cgroup path | `/sys/fs/cgroup` | `scripts/eject-watch-check.sh:36`, `scripts/eject-watch-check.sh:365` |
| `START_WAIT_SECS` | Start readiness/evidence polling window, seconds | `20` | `scripts/eject-watch-check.sh:37`, `scripts/eject-watch-check.sh:271`, `scripts/eject-watch-check.sh:286` |
| `SETTLE_SECS` | Scanner-off start-path settling delay, seconds | `2` | `scripts/eject-watch-check.sh:38`, `scripts/eject-watch-check.sh:304` |
| `LIVE_HOLD_SECS` | Live watcher observation window, seconds | `10` | `scripts/eject-watch-check.sh:39`, `scripts/eject-watch-check.sh:348` |
| `STOP_WARN_SECS` | Stop duration warning threshold, seconds | `10` | `scripts/eject-watch-check.sh:40`, `scripts/eject-watch-check.sh:405` |
| `STOP_FAIL_SECS` | Stop duration failure threshold, seconds | `30` | `scripts/eject-watch-check.sh:41`, `scripts/eject-watch-check.sh:404` |
| `STOP_WALL_SECS` | Timeout for stop command, seconds | `130` | `scripts/eject-watch-check.sh:42`, `scripts/eject-watch-check.sh:166` |
| `CGROUP_WAIT_SECS` | Post-stop cgroup empty polling window, seconds | `5` | `scripts/eject-watch-check.sh:43`, `scripts/eject-watch-check.sh:377` |
| `EJECT_WATCH_UNIT_DIR` | Installed/expected unit directory | Normal XDG unit directory above | `scripts/install-eject-watch.sh:144`; `scripts/eject-watch-check.sh:489` |

## Other reads and exported settings

| Variable | Direction and behavior | Source |
| --- | --- | --- |
| `PATH` | Standard executable lookup is implicit in shell commands and installer `command -v`. Installer's host import probe **sets** a clean `PATH=/usr/local/bin:/usr/bin:/bin`; distrobox service **sets** `%h/.local/bin:/usr/local/bin:/usr/bin:/bin`. These fixed settings are not configurable reads. | `scripts/install-eject-watch.sh:86`, `scripts/install-eject-watch.sh:101`; `packaging/systemd/opticfilm-eject-watch.service:26` |
| `PYTHONPATH` | **Set**, replacing inherited value, by `hw_run.sh` (driver plus overlay), container watcher (`.`), generated host unit (driver plus overlay), and generated first-load help command (`.`). The help command is text for the user to run. | `scripts/hardware/hw_run.sh:27`; `packaging/systemd/eject-watch-run.sh:19`; `packaging/systemd/opticfilm-eject-watch-host.service:19`; `src/opticfilm_linux/app/messages.py:118` |
| `OF135I_DUMP_CAL` | **Exported** as `1` for the external driver; not read by this repository’s code. External driver behavior: see that driver. | `scripts/hardware/hw_run.sh:28` |
| `OF135I_TRACE` | **Set** for the external driver's scan command to `<attempt>/trace-<name>.jsonl`; not read by this repository's code. External driver's accepted behavior: see that driver. | `scripts/hardware/hw_run.sh:32` |
| `HEAD_SHA` | **Set** by the merge helper from the PR head and immediately **read** by its embedded Python to compare CI runs; internal handoff, not a caller setting. | `scripts/hardware/merge_when_green.sh:17`, `scripts/hardware/merge_when_green.sh:19` |
| `OPTICFILM_DRIVER_DIR`, `FAKE_DRIVER_SYNTH`, `XDG_STATE_HOME`, `XDG_CACHE_HOME` | Rehearsal **sets** these to the fake driver, `1`, `<work>/state`, and `<work>/cache`. `FAKE_DRIVER_SYNTH` and the other `FAKE_DRIVER_*` switches (`MODE`, `EJECT_MODE`, `LOAD_EXIT`, `LOAD_DELAY`, `DELAY`, and similar) are read only by the test fake driver in `tests/fake_driver/of135i/__main__.py`, never by `src/`. | `scripts/rehearse.py:172`, `scripts/rehearse.py:173`, `scripts/rehearse.py:174`, `scripts/rehearse.py:175` |
| `LC_ALL` | Installer **sets** `C` for path/container-name validation subprocesses; not a configurable read. | `scripts/install-eject-watch.sh:30`, `scripts/install-eject-watch.sh:121` |
| `PWD`, `FLATPAK_DEST` | Flatpak build command **reads** builder-provided paths for pip wheel lookup and install prefix. No default in the manifest; see the Flatpak build environment for their values. | `packaging/flatpak/io.github.bearyjd.OpticFilmLinux.json:35` |

Removed: `QT_QUICK_CONTROLS_STYLE` is no longer read; the app forces the `Basic` Quick Controls style (`src/opticfilm_linux/app/qt/app.py:42`, `:168`). Flatpak detection uses the `/.flatpak-info` file, not an environment variable (`src/opticfilm_linux/app/sandbox.py`).

Bash bookkeeping such as `BASH_SOURCE`, `SECONDS`, `PIPESTATUS`, and `BASH_REMATCH` is shell state rather than supported caller configuration. `UV_PROJECT_ENVIRONMENT` appears in a printed setup suggestion in `scripts/opticfilm-here:10`, not an environment read or assignment executed by that script. Python home-directory defaults use `Path.home()`; its platform-specific environment handling is delegated to Python.
<!-- /AUTO-GENERATED -->
