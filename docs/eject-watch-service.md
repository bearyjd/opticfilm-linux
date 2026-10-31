# Eject button without the app (systemd user service)

The scanner's front-panel Eject button only reports while something polls the scanner's
interrupt endpoint. That is either the driver's `of135i watch` command or the running app. This
optional systemd **user** unit keeps `of135i watch` running, so the button works whenever the
scanner is on, without the app open and without typing a command.

The unit is `packaging/systemd/opticfilm-eject-watch.service`. It starts `of135i watch` inside
the distrobox container `dev` (where the driver's `pyusb` lives) through `distrobox-enter`,
which must be on the user manager's `PATH` (see below), and the container needs `pkill` and
`pgrep` (the `procps` package). The unit uses `Restart=always` and retries every 30 seconds
(backing off to 2 minutes on systemd 254
or newer), so it picks the scanner up within a minute or two of switching it on; no udev rule is
needed. The wait is deliberate: each retry starts a container shell and touches USB.

## Install

1. Copy the unit and edit the driver folder:

   ```sh
   mkdir -p ~/.config/systemd/user
   cp packaging/systemd/opticfilm-eject-watch.service ~/.config/systemd/user/
   ```

   In the copy, replace `@DRIVER_DIR@` with the path of your driver checkout (the same
   folder as `OPTICFILM_DRIVER_DIR`), keeping the double quotes, for example
   `"/path/to/opticfilm135i-linux"`. Spaces are fine; write a literal `%` as `%%` and a literal
   `$` as `$$`. Change the container name `dev` if yours differs.

   The unit sets `PATH` to start with `~/.local/bin`, because the user manager's own `PATH`
   usually lacks it and a per-user distrobox lives there. Edit the `Environment=PATH=` line if
   distrobox is elsewhere.

2. Load and start it:

   ```sh
   systemctl --user daemon-reload && systemctl --user enable --now opticfilm-eject-watch.service
   ```

3. To have it start at boot without logging in, run `loginctl enable-linger "$USER"`
   (optional).

Check it with `systemctl --user status opticfilm-eject-watch.service` and
`journalctl --user -u opticfilm-eject-watch.service`.

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
- The unit is not started again while the scanner is busy: closing a batch waits (up to two
  minutes) for an eject or load that is still moving the holder, and for a status request that
  is still out. If they have not ended by then, the unit stays stopped and a banner gives the
  `systemctl --user start` command to run when the scanner is idle.
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

- **SIGINT only.** `of135i watch` must never be killed: a kill during an eject can land
  mid-move. The unit sets `KillSignal=SIGINT`, `SendSIGKILL=no` and `TimeoutStopSec=120`, and
  never sends SIGKILL. On the host the unit's main process is `distrobox-enter`'s shell, and the
  Python process runs in the container, outside the unit's control group, so a signal sent to
  the main process does not reach it. The unit therefore has an `ExecStop=` that runs inside the
  container, sends SIGINT to the `of135i watch` process with `pkill -INT` and waits up to about
  100 seconds for it to exit. If a stop still times out, the unit is marked `failed` with
  `watch` possibly alive; the app's process check above catches that.
  **Unverified:** none of this has been run on a real system. That SIGINT sent this way ends
  `watch` cleanly, that `distrobox-enter --no-tty -n dev` accepts these arguments, and that
  `pkill` exists in your container are all untested. Check them with the procedure below, with
  the scanner switched off, before relying on the unit.
- **No-motor check of the stop path** (scanner switched OFF, so `watch` cannot move anything):

  1. Start the unit: `systemctl --user start opticfilm-eject-watch.service`. With the scanner
     off, `watch` finds no device and the unit may restart every 30 seconds; run the next steps
     right after a start.
  2. Confirm a process exists: `ps -eo pid,args | grep "[o]f135i"` should show
     `python3 -m of135i watch`.
  3. Stop it: `time systemctl --user stop opticfilm-eject-watch.service`. It should return
     within a few seconds.
  4. Confirm the process is gone (the `ps` command from step 2 prints nothing) and that
     `systemctl --user is-active opticfilm-eject-watch.service` prints `inactive`, not `failed`.
     The journal (`journalctl --user -u opticfilm-eject-watch.service`) should show no SIGKILL.

  If step 3 hangs or step 4 shows a surviving process or `failed`, do not use the unit during
  an eject; end any leftover `watch` with Ctrl-C or `kill -INT`, never `kill -9`.
- **Not idle-homed at power-on.** If the scanner is not idle-homed when `watch` starts, `watch`
  refuses (its start-state check) and exits, and the unit just retries. The unit does not use
  `--cold-homing-rounds` and relaxes no guard or timeout. The button therefore stays inactive
  until the scanner is loaded or power-cycled into a clean idle state.
- If the app is killed outright, it cannot start the watcher again; run
  `systemctl --user start opticfilm-eject-watch.service` yourself.
