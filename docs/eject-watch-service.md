# Eject button without the app (systemd user service)

The scanner's front-panel Eject button only reports while something polls the scanner's
interrupt endpoint. That is either the driver's `of135i watch` command or the running app. This
optional systemd **user** unit keeps `of135i watch` running, so the button works whenever the
scanner is on, without the app open and without typing a command.

It runs `of135i watch` in one of two ways, chosen by the installer (`--runner`):

- **Host (bare metal, no container; the default when it works).** The unit
  `packaging/systemd/opticfilm-eject-watch-host.service` runs the host's `python3 -m of135i
  watch` from the driver checkout. The host `python3` needs `pyusb` (Fedora: `sudo dnf install
  python3-pyusb`; otherwise `python3 -m pip install --user pyusb`) and your user needs access to
  the USB device (a udev ACL does this on a desktop). The main process is Python itself, so
  systemd's own stop sends it SIGINT; there is no `ExecStop`, no `SIGKILL` (`KillMode=process`,
  `SendSIGKILL=no`) and no container needed.
- **Distrobox (optional).** The unit `packaging/systemd/opticfilm-eject-watch.service` starts
  `of135i watch` inside a distrobox container (default `dev`, where the driver's `pyusb` lives)
  through `distrobox-enter`, which must be on the user manager's `PATH`, and the container needs
  `bash`, `python3`, `pkill` and `pgrep` (the `procps` package). Details below.

Both use `Restart=always` and retries every 30 seconds
(backing off to 2 minutes on systemd 254 or newer), so it picks the scanner up within a minute
or two of switching it on; no udev rule is needed. The wait is deliberate: each retry starts a
container shell and touches USB.

For the distrobox runner the work is done by two small scripts that run inside the container:
`packaging/systemd/eject-watch-run.sh <driver_dir>` runs `of135i watch` from the driver
checkout, and `packaging/systemd/eject-watch-stop.sh` stops it (see "SIGINT only" below). The
checkout and the driver folder must both be visible inside the container at the same path, which
is the case for anything under your home folder.

## Install

1. Render the unit for this checkout and your driver folder:

   ```sh
   scripts/install-eject-watch.sh [--runner auto|host|distrobox[:NAME]] /path/to/opticfilm135i-linux
   ```

   With no folder it uses `OPTICFILM_DRIVER_DIR`. `--runner` defaults to `auto`: host if the
   host `python3` can import `pyusb` and `of135i.cli` the way the unit will run it (a clean
   environment, `PYTHONPATH` set to the driver folder and the optional overlay, started in the
   driver folder; your own `PYTHONPATH` does not count), else `distrobox:dev` if `distrobox-enter` exists, else a
   clear error. The installer prints which runner it chose and why; the names match the app's
   `OPTICFILM_DRIVER_RUNNER` (`host`, `distrobox:<name>`). `OF135I_DEPS_OVERLAY` (optional) adds
   a folder of extra Python packages to the host unit's `PYTHONPATH`. All folders and the
   `python3` path must be absolute and made only of letters, digits and `_ . / -`; anything else
   (spaces, `%`, `$`, quotes) is refused with a message and nothing is written. The installer writes
   `~/.config/systemd/user/opticfilm-eject-watch.service` (it shows a diff when it replaces an
   older one) and runs `systemctl --user daemon-reload`. It does **not** enable or start the
   unit. Change the container name `dev` in the written unit if yours differs.

   **Switching runner or changing the unit while it runs is refused.** Both runners share one
   unit name, and a watcher started by the old definition could not be stopped by the new one.
   So the installer stops with a message when `systemctl --user is-active` is `active`,
   `activating`, `deactivating` or `reloading`, or when any `of135i watch` process exists, unless
   the rendered unit is byte-identical to the installed one. Run
   `systemctl --user stop opticfilm-eject-watch.service`, make sure no `of135i watch` process is
   left (end it with Ctrl-C or `kill -INT`, never `kill -9`), then install again.

   The distrobox unit sets `PATH` to start with `~/.local/bin`, because the user manager's own `PATH`
   usually lacks it and a per-user distrobox lives there. Edit the `Environment=PATH=` line if
   distrobox is elsewhere.

2. Enable and start it:

   ```sh
   systemctl --user enable --now opticfilm-eject-watch.service
   ```

3. To have it start at boot without logging in, run `loginctl enable-linger "$USER"`
   (optional).

Check it with `systemctl --user status opticfilm-eject-watch.service` and
`journalctl --user -u opticfilm-eject-watch.service --since "-5min"`. If the unit has failed,
clear that with `systemctl --user reset-failed opticfilm-eject-watch.service` before starting it
again.

**Do not put shell syntax after `--` in the unit.** `distrobox-enter` does not keep argument
boundaries: it joins everything after `--` into one string and `eval`s it. Quotes, `$`,
`$(...)`, parentheses, `;`, `|`, `&`, redirections, backslashes and spaces there are lost or are
a syntax error (an earlier version of this unit failed exactly that way). So the unit passes
only plain paths, and anything that needs logic goes in the two scripts. A test checks this.

## Uninstall

```sh
systemctl --user disable --now opticfilm-eject-watch.service
rm ~/.config/systemd/user/opticfilm-eject-watch.service
systemctl --user daemon-reload
```

## How the app interacts with it

Only one program may use the scanner at a time, so the app steps the watcher aside:

- Before it opens the scanner (opening or creating a batch that can scan) it runs
  `systemctl --user stop opticfilm-eject-watch.service`.
- When the batch is closed, or the window exits, it starts the unit again, but **only if it was
  running before**. A unit you stopped yourself stays stopped.
- If `systemctl` is missing, the unit is not installed, or it is not running, the app does
  nothing and shows no error.
- A failure to start the unit again never crashes the app; it shows a one-line banner and logs
  the detail. A failure to stop it is not a banner: if the unit is still running afterwards
  (or its state cannot be read after a stop was requested), the app **refuses to open the
  scanner** and says so. Stop the unit by hand and try again.
- Closing, opening or creating a batch is refused ("Wait for the eject/load to finish") while
  an eject or load is moving the holder, so the unit cannot be started again during a move that
  way. As a backstop the unit is also never started while the scanner is busy: a batch change
  accepted just before a move began, or a scanner status request still out, makes the app
  wait (up to two minutes, and not at all once the window is closing) and then leave the unit
  stopped with a banner that gives the `systemctl --user start` command to run when the scanner
  is idle.
- The unit is not started again while an `of135i watch` process is already running, so two
  watchers never run at once.
- Read-only batches and demo mode (`--fake`) never touch the unit.
- Switching from one batch to another keeps the unit stopped in between; it is started again
  only when no batch with a scanner is left open.
- **A leftover `watch` also blocks the scanner.** Stopping the unit can leave `of135i watch`
  running outside it (the unit uses `KillMode=process`, and a timed-out stop leaves the unit
  `failed` with the process alive). So after the stop, and also when `systemctl` or the user
  session bus is missing, the app reads `/proc/*/cmdline` for an `of135i watch` process
  (`python -m of135i watch`, `-mof135i`, `of135i.__main__`, a console script, options in
  between). If it finds one, or cannot list `/proc` at all, it refuses to open the scanner and
  says so; a `watch` you started by hand counts too. The app never kills it: end it with
  Ctrl-C in its terminal. A distrobox container shares the host's process namespace by default,
  so the host sees the watcher inside it. The blind spot is an app that runs in its own process
  namespace (a Flatpak sandbox, or a container started with `--unshare-process`): it cannot see
  host processes, and it has no `systemctl` for the unit either, so there the check cannot
  protect you and the unit should not be used together with it.

## Things to know

- **The unit holds the scanner.** Stop it (`systemctl --user stop opticfilm-eject-watch.service`)
  before running driver commands such as `load` or `scan` from a terminal, and start it again
  afterwards; the app does this by itself, the command line does not.
- **SIGINT only.** `of135i watch` must never be killed: a kill during an eject can land
  mid-move. The unit sets `KillSignal=SIGINT`, `SendSIGKILL=no` and `TimeoutStopSec=120`, and
  never sends SIGKILL. On the host the unit's main process is `distrobox-enter`'s shell, and the
  Python process runs in the container, outside the unit's control group, so a signal sent to
  the main process does not reach it. The unit therefore has an `ExecStop=` that runs the stop script inside the
  container, which sends SIGINT to the `of135i watch` process with `pkill -INT` and waits up to about
  100 seconds for it to exit (`eject-watch-stop.sh`). Because `pkill -f` matches full command lines in the shared process
  namespace, it also signals the host-side `distrobox-enter` command line and a `watch` you
  started by hand; SIGINT to those is the intended outcome. Any unrelated host process with `of135i ... watch` in its command line (an editor or `grep`, say) would be signalled the same way. If a stop still times out, the unit is marked `failed` with
  `watch` possibly alive; the app's process check above catches that.
  **Verified by tests only, not on a real system:** the scripts, the installer and the rule that
  the unit passes only plain words after `--` are tested offline (with stand-ins for `pkill`
  and `pgrep`). Nothing about `distrobox-enter` itself is verified: that these arguments are
  accepted, that SIGINT sent this way ends `watch` cleanly, and that `pkill` exists in your
  container all wait for the no-motor check below, run with the scanner switched off.
- **Checking the unit with one command** (`scripts/eject-watch-check.sh`). Two modes:

  ```sh
  scripts/eject-watch-check.sh [--enable] /path/to/opticfilm135i-linux         # scanner OFF
  scripts/eject-watch-check.sh --live [--enable] /path/to/opticfilm135i-linux  # scanner ON
  ```

  The driver folder defaults to `OPTICFILM_DRIVER_DIR`. Output is one line per step and a single
  last line, `PASS`, `FAIL` or `INCONCLUSIVE` (exit 0, 1 and 5; 2 usage; 3 the scanner state is
  wrong or unknown; 4 an `of135i watch` already runs or `/proc` cannot be read). It never sends a
  command to the scanner, never presses or simulates a button, and never sends any signal.
  `--enable` runs `systemctl --user enable` only after a full `PASS` of the mode that ran, and
  never starts the unit.

  `--runner` (default `auto`) is passed to the installer, so the check works for both runners.
  With the host runner the watcher is the unit's `MainPID`, and `--live` also checks that
  `/proc/<MainPID>/cmdline` is `of135i watch`.

  What each mode proves:

  - **Scanner OFF (default).** Proves the start path works (through `distrobox-enter` for that
    runner; for the host runner that the unit starts `python3 -m of135i watch`; the driver's
    own output is in the journal and none of the old failure signatures such as `null directory`
    or `syntax error`) and that the stop from the failed/restarting state is fast, leaves
    `inactive` and never needs SIGKILL. It does **not** prove the SIGINT stop of a live watcher:
    with the scanner off `watch` exits within a second, so there is no live process to stop.
  - **`--live` (scanner ON, idle-homed).** Do not press the Eject button during it. The watcher
    must stay up for 10 seconds under the unit (its `MainPID` is alive), then the stop must be
    clean and fast. If `watch` exits by itself (for example the scanner is not idle-homed) the
    result is `INCONCLUSIVE`, never `PASS`. This is the only mode that proves the live stop path.

  The scanner-state guard cannot be bypassed by a flag or by the environment: the test overrides
  (`SYSFS_USB_ROOT` and the like) are ignored unless `EJECT_WATCH_CHECK_TESTING=1`, an unreadable
  or empty USB listing counts as "cannot tell" and refuses, and a refusal makes no `systemctl`
  call.

  A start only counts when the journal shows the driver's own startup output (`not found on the
  host USB bus`, `watching for button events` or `opened device`); a `Traceback`,
  `ModuleNotFoundError`, `No module named`, `status=127` or `status=203` anywhere in the journal
  since the start fails the run even if such output is also there. For the host runner it
  first re-runs the installer's import probe.

  On `FAIL` or `INCONCLUSIVE` it prints the journal tail, runs `systemctl --user disable` (and
  says so if the unit was enabled before the check) and a
  further bounded `stop` if the unit is still up, then reports the states it actually read
  (`is-active`, `is-enabled`, watcher process). If something is still running, end it with
  Ctrl-C or `kill -INT`, never `kill -9`, and paste the output.

  <details>
  <summary>What it does</summary>

  1. Refuses by the scanner-state rule above, and if another `of135i watch` already runs. Runs
     `systemctl --user reset-failed`, then `scripts/install-eject-watch.sh`.
  2. Starts the unit. Scanner OFF: waits up to about 20 s for driver output in the journal since
     the start (or `ExecMainStartTimestamp`), and fails on `null directory`, `syntax error`,
     `No such file`, `command not found` or `Permission denied`. `--live`: waits for the process
     and holds it for 10 s.
  3. Times `systemctl --user stop` (hard limit about 130 s; over 30 s is a `FAIL`, over 10 s a
     warning).
  4. Checks no watcher process remains, `is-active` prints exactly `inactive`, this run's journal
     has no `SIGKILL`, `status=9/KILL` or `code=killed`, and the unit's cgroup holds no leftover
     process (skipped with a note if unreadable). "Unit process ... remains running after unit
     stopped" lines are tolerated.
  5. Only with `--enable` and only after all of that passed: `systemctl --user enable`.

  </details>
- **A restart after a failed eject.** The driver deliberately sends nothing on a further button
  press once an eject has failed and left the scanner not idle-homed (state `0x22`). But both
  units use `Restart=always`: when `watch` exits, systemd starts a new one after 30 seconds, and
  that new `watch` reads the scanner afresh. If it accepts the scanner's state (it refuses
  unless idle-homed), it acts on the next press. So the driver's "a further press must not send
  anything" holds inside one `watch` process, not across the 30-second restart. This has not
  been tested on hardware; if it worries you, stop the unit after a failed eject
  (`systemctl --user stop opticfilm-eject-watch.service`) until you have looked at the scanner.
- **Not idle-homed at power-on.** If the scanner is not idle-homed when `watch` starts, `watch`
  refuses (its start-state check) and exits, and the unit just retries. The unit does not use
  `--cold-homing-rounds` and relaxes no guard or timeout. The button therefore stays inactive
  until the scanner is loaded or power-cycled into a clean idle state.
- If the app is killed outright, it cannot start the watcher again; run
  `systemctl --user start opticfilm-eject-watch.service` yourself.

## What has been verified on a real system

- Distrobox runner: one system, 2026-10-31. The scanner-off start path, and the `--live` test
  (SIGINT stop of a live watcher, driver printed "stopped", about 1.75 s, unit `inactive`, no
  SIGKILL).
- Host runner: **not verified on a real system** until you run
  `scripts/eject-watch-check.sh` (and then `--live`) on it. The offline tests cover the rendered
  unit, the installer's runner choice and the check script against stubs only.
