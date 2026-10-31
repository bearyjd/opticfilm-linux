# Eject button without the app (systemd user service)

The scanner's front-panel Eject button only reports while something polls the scanner's
interrupt endpoint. That is either the driver's `of135i watch` command or the running app. This
optional systemd **user** unit keeps `of135i watch` running, so the button works whenever the
scanner is on, without the app open and without typing a command.

The unit is `packaging/systemd/opticfilm-eject-watch.service`. It starts `of135i watch` inside
the distrobox container `dev` (where the driver's `pyusb` lives), the same way the app's
first-load command does, and uses `Restart=always`. It retries every 30 seconds (backing off to 2 minutes on systemd 254
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
- A failure to stop or start the unit never crashes the app; it shows a one-line banner and
  logs the detail. A failed start is only a banner.
- If the stop was requested and the unit is still running afterwards, the app **refuses to open
  the scanner** and says so. Stop the unit by hand and try again.
- Read-only batches and demo mode (`--fake`) never touch the unit.
- Switching from one batch to another keeps the unit stopped in between; it is started again
  only when no batch with a scanner is left open.
- **A leftover `watch` also blocks the scanner.** Stopping the unit can leave `of135i watch`
  running outside it (the unit uses `KillMode=process`, and a timed-out stop leaves the unit
  `failed` with the process alive). So after the stop, and also when `systemctl` or the user
  session bus is missing, the app reads `/proc/*/cmdline` for an `of135i watch` process. If it
  finds one it refuses to open the scanner and says so; a `watch` you started by hand counts
  too. The app never kills it: end it with Ctrl-C in its terminal. The check only sees a
  container that shares the host's process namespace, which distrobox does by default.

## Things to know

- **SIGINT only.** `of135i watch` must never be killed: a kill during an eject can land
  mid-move. The unit sets `KillSignal=SIGINT`, `SendSIGKILL=no` and `TimeoutStopSec=120`. A stop
  therefore waits up to two minutes and then the unit is marked failed rather than killed, with
  `watch` possibly still alive; the app's process check above catches that.
  *Unverified:* whether SIGINT sent to `distrobox enter` reaches the Python process inside the
  container (the unit uses `exec` so no shell sits in between). Test it on your setup with
  `systemctl --user stop` and the journal before relying on it during an eject. The unit's
  `distrobox enter --no-tty` flag is also unverified here.
- **Not idle-homed at power-on.** If the scanner is not idle-homed when `watch` starts, `watch`
  refuses (its start-state check) and exits, and the unit just retries. The unit does not use
  `--cold-homing-rounds` and relaxes no guard or timeout. The button therefore stays inactive
  until the scanner is loaded or power-cycled into a clean idle state.
- If the app is killed outright, it cannot start the watcher again; run
  `systemctl --user start opticfilm-eject-watch.service` yourself.
