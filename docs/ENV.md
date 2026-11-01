# Environment variables

<!-- AUTO-GENERATED -->
Generated from environment reads and assignments in `src/`, `scripts/`, and `packaging/`. Source references are repository-relative `file:line`. Optional means the caller may omit the variable; it does not imply that every supplied value is validated. External driver and Qt behavior beyond these reads is outside this inventory.

## App runtime

| Variable | Required? | Purpose; accepted values and default | Source |
| --- | --- | --- | --- |
| `OPTICFILM_DRIVER_RUNNER` | Optional | Chooses the runner for the first-load **help command**. Whitespace is stripped. `host` runs directly; `distrobox:NAME` accepts a name matching `[A-Za-z0-9][A-Za-z0-9_.-]*`. Unset, empty, or invalid values use container `dev`; invalid nonempty values are shown in the help note and logged when the command is generated. This does not select the scanner subprocess runner. | `src/opticfilm_linux/app/messages.py:101` |
| `OPTICFILM_LOAD_COMMAND` | Optional | Nonempty stripped text replaces the first-load command shown in help; empty/unset generates the normal command. | `src/opticfilm_linux/app/qt/app.py:57`, `src/opticfilm_linux/app/qt/app.py:66` |
| `QT_QUICK_CONTROLS_STYLE` | Optional | Any nonempty value prevents the app from choosing its KDE desktop style. Actual style-name interpretation belongs to Qt; see Qt for accepted names. Without it, the app selects `org.kde.desktop` when found. | `src/opticfilm_linux/app/qt/app.py:90` |
| `OPTICFILM_NEXT_STRIP` | Optional | Exactly `1` enables the hardware-unproven `load --next-strip` path; every other value/unset disables it. | `src/opticfilm_linux/scanner/select.py:21` |
| `OPTICFILM_EJECT_BUTTON` | Optional | Front-panel Eject support is enabled by default. Stripped, case-insensitive `0`, `false`, `no`, or `off` disables it; all other values leave it enabled. | `src/opticfilm_linux/scanner/select.py:26` |

## Driver and scanner

| Variable | Required? | Purpose; accepted values and default | Source |
| --- | --- | --- | --- |
| `OPTICFILM_DRIVER_DIR` | Optional in app | Driver checkout containing an `of135i` directory. Empty/unset uses an importable `of135i` on the Python path. Also supplies the generated help command's directory. `scripts/opticfilm-here` exports a default of `<repo>/.local-research/ref/opticfilm135i-linux` when empty/unset. | `src/opticfilm_linux/scanner/driver.py:86`, `src/opticfilm_linux/scanner/driver.py:95`; `src/opticfilm_linux/app/qt/app.py:60`; `scripts/opticfilm-here:7` |
| `XDG_STATE_HOME` | Optional | Driver logs use `<value>/opticfilm-linux/logs`, defaulting to `~/.local/state/opticfilm-linux/logs`. Interrupted-scan state uses `<value>/opticfilm-linux` only for a nonempty absolute value; otherwise it uses `~/.local/state/opticfilm-linux`. The driver log configuration does not apply that absolute-path check. | `src/opticfilm_linux/scanner/driver.py:87`; `src/opticfilm_linux/scanner/interrupted.py:43` |
| `XDG_CACHE_HOME` | Optional | Scan scratch directory is `<value>/opticfilm-linux/work`; default `~/.cache/opticfilm-linux/work`. Used both by environment configuration and the scratch fallback. No path validation at these reads. | `src/opticfilm_linux/scanner/driver.py:88`, `src/opticfilm_linux/scanner/driver.py:113` |
| `PYTHONPATH` | Optional | Existing value is inherited by driver subprocesses; when a driver directory is configured, that directory plus a path separator is prepended. Other environment entries are copied unchanged. | `src/opticfilm_linux/scanner/driver.py:603`, `src/opticfilm_linux/scanner/driver.py:606` |

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
| `OPTICFILM_HW_ASSUME_YES` | Optional | Any nonempty value bypasses interactive confirmation in both hardware experiment scripts, intended for supervised wrappers; empty/unset requires terminal confirmation. | `scripts/hardware/ab_gate_test.sh:19`; `scripts/hardware/tray_and_armc_test.sh:19` |
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
| `OPTICFILM_DRIVER_DIR`, `FAKE_DRIVER_SYNTH`, `XDG_STATE_HOME`, `XDG_CACHE_HOME` | Rehearsal **sets** these to the fake driver, `1`, `<work>/state`, and `<work>/cache`. `FAKE_DRIVER_SYNTH` is not read under the inventoried source directories; see the fake driver for its consumer. | `scripts/rehearse.py:172`, `scripts/rehearse.py:173`, `scripts/rehearse.py:174`, `scripts/rehearse.py:175` |
| `LC_ALL` | Installer **sets** `C` for path/container-name validation subprocesses; not a configurable read. | `scripts/install-eject-watch.sh:30`, `scripts/install-eject-watch.sh:121` |
| `PWD`, `FLATPAK_DEST` | Flatpak build command **reads** builder-provided paths for pip wheel lookup and install prefix. No default in the manifest; see the Flatpak build environment for their values. | `packaging/flatpak/io.github.bearyjd.OpticFilm.json:27` |

Bash bookkeeping such as `BASH_SOURCE`, `SECONDS`, `PIPESTATUS`, and `BASH_REMATCH` is shell state rather than supported caller configuration. `UV_PROJECT_ENVIRONMENT` appears in a printed setup suggestion in `scripts/opticfilm-here:10`, not an environment read or assignment executed by that script. Python home-directory defaults use `Path.home()`; its platform-specific environment handling is delegated to Python.
<!-- /AUTO-GENERATED -->
