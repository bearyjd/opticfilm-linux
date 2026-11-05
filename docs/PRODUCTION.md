# Production runbook: scanning many slides

For the owner scanning boxes of slides at 7200 dpi on one OpticFilm 135i. Short steps, expected
times, and what to do when something goes wrong. Facts come from the owner's and maintainer's
sessions of 2026-11-04/05, the README, the docs and the team learnings. Anything not checked is
marked **unverified**.

This is not the developer or host-ops guide. For that see [RUNBOOK.md](RUNBOOK.md) and
[ENV.md](ENV.md).

## Safety rules (read once)

- Stay at the scanner while it moves.
- If the motor grinds or stalls: switch the scanner **OFF at once**. Send nothing else.
  The read-only `of135i doctor` is only useful **before** a power cycle, and only when it is
  safe and the scanner still responds. After power-off it can only say "scanner not found".
  Do not switch the scanner on again until you have inspected the holder, then restart from
  the checklist ([hardware-checklist.md](hardware-checklist.md), [RUNBOOK.md](RUNBOOK.md)).
- Never force a holder that will not come out.
- Never use `pkill` or `pgrep -f` to stop a scan or the watcher.
- "Ejected" in a message does not prove the holder is free. Look at it.
- Keep the calibration stop gate on. Do not turn it off to "fix" a scan.
- The production driver (`.local-research/publish/driver-latest`, the owner's fork at commit `eaa6f88`)
  has the stop gate on by default: `scan --cal-wait-scanstop` defaults to `enforce`
  (`of135i/cli.py`). It is behind the upstream master by many commits, but upstream ported the gate
  from this fork, and upstream's own default is `off`. To check a driver, run
  `grep -n 'cal-wait-scanstop' -A2 <driver>/of135i/cli.py` and look for `default="enforce"`.

## 1. Before you start

Tick each item.

- [ ] Disk: the batch folder is **outside the repo**, on disk, not in RAM-backed `/tmp`. Allow
      about 3.5 GB of raw data per 7200 dpi tray, and about 9 GB free for a first tray (README,
      RUNBOOK.md, performance.md). More if you keep raw scans for many trays.
- [ ] Nothing heavy is running. The driver does about 2 min of single-core post-processing per
      frame during a scan.
- [ ] The Eject watcher is not holding the scanner. The app stops
      `opticfilm-eject-watch.service` itself and restores it on exit (only if it was running).
      For terminal driver commands you must stop it yourself (section 6).
- [ ] Scanner has power and USB. After a power cycle, follow the cold start in step 2.1.
- [ ] Holder is clean and loaded with up to four slides.
- [ ] Decide the batch settings: 7200 dpi is the default for new batches. **Export JPEGs after
      each tray** is off by default. **Keep raw scans after export** keeps the raw scans (they are already driver-cleaned when the
      driver clean ran).
- [ ] Start the app with the production command:

```sh
distrobox-host-exec env PYTHONPATH=<repo>/src \
  OPTICFILM_DRIVER_DIR=<repo>/.local-research/publish/driver-latest \
  OPTICFILM_DRIVER_RUNNER=host \
  python3 -c "from opticfilm_linux.app.qt.app import main; raise SystemExit(main())"
```

Replace `<repo>` with the repo path. Create or open the batch **outside** the repo.

## 2. Scan one tray

Times are for one 4-slide tray at 7200 dpi.

### 2.1 Cold start (after any power-on or power cycle)

The scanner is "cold" after a power cycle. The app says "Scanner is not loaded" until you load.

1. Switch the scanner **OFF**.
2. Wait until the lights go out **and** `07b3:1436` is gone from the USB bus
   (for example `lsusb | grep 07b3`).
3. Switch it **ON**.
4. Wait for the power light to stop blinking. A steady light means ready. (Steady blue is an
   owner observation, 2026-11-05; the repo does not name the colour. **Unverified.**)
5. Slide the holder into the slot loosely. Do not push it in hard yet.
6. In the app, press **First load…** (the primary button). The card says "Slide the holder in
   loosely, then press Load." Press **Load**.
7. When the card says "Pull the holder fully OUT, then push it back in to the stop. Press
   Done.", do that, then press **Done**. Wait while it says "Moving the holder…" and
   "Loading…". It ends with "Loaded. Press Scan when ready."
8. Check by hand that the holder is latched. The latch has no sensor.

**Cancel** needs a power cycle afterwards (the app asks "Cancel first load?"). Press it only
if you must. If the card says "First load did not finish", read the error details and power
cycle before trying again.

Status: this in-app cold first load passed twice in a prototype (2026-11-02). It has **not**
been run cold through the finished app. See section 7.

### 2.2 Scan

1. Load the holder (cold start above, or **Load next holder** between trays).
2. Press **Scan** when the scanner says "Scanner ready".
3. Wait. Do not run other heavy jobs.

| Setting | Time per 4-slide tray |
|---|---|
| 7200 dpi, driver run with `--no-clean` | about 14 min |
| 7200 dpi, driver's own IR clean | about 22 min |
| 3600 dpi | about 6 min |

- The app **currently still runs the driver's dust clean**, so expect about 22 min at 7200 dpi.
  A pending PR (#95) makes the app pass `--no-clean` by default (restore the clean with
  `OPTICFILM_DRIVER_CLEAN=1`). After it merges, a 4-slide 7200 dpi tray should take about
  14 min. That is expected, not yet re-measured.
- The scan pass is 175 s per frame at 7200 dpi. The rest is post-processing, about 2 min of
  single-core work per frame.
- The 22 min and 2 min figures were measured on one unit, on the owner's 2026-11-04 trays
  (diag.json started/finished against file times).
- The earlier 827 s and 854 s trays (learnings) were driver runs with `--no-clean`, which is
  why they are close to the 14 min figure.

### 2.3 After the scan

- The holder **stays latched** after a scan. Auto-eject is not implemented.
- Eject with the app's **Eject holder** button, or the scanner's front-panel Eject button.
  The panel button works while the app window is idle, not during a scan or holder move.
- Look at the holder. Confirm it is released before you pull it.

## 3. Review, export and swap trays

Enhancement runs in the background while the next tray scans (README).

1. Open the finished tray. Slides with no flag are accepted automatically. Flagged ones
   (very dark, very bright, clipped, dusty) wait for you.
2. Check each flagged slide. Use rotate and mirror if needed.
   - Rotating one slide in review used to take about 19 s. A fix is in progress.
   - Preview lag does not change the output. **Export recomputes from the raw data.**
3. Press **Accept all** when satisfied.
4. **Export.** JPEG is the output format for the heirloom workflow. Files go to
   `box-<N>/<section>/<NNN>.jpg` when the heirloom layout is chosen (spec; the layout code is
   merged).
5. Swap trays:
   1. **Eject holder**; look and confirm it is released.
   2. Swap the holder and push it in to the stop.
   3. Press **Load next holder**. This runs `load --next-strip`. It took 5 to 6 s in a
      five-swap series on one unit, with no jog and no prompt.
   4. Press **Scan**.

Prefer not to export while a tray scans: the driver needs a core for post-processing. One
7200 tray was clean while a 7200 export ran (learnings, 2026-11-01), so it is allowed, but
that is one observation.

Free disk space only with the app's **Free disk space** dialog. It says what it removes before
it does. Raw scans are removed only by that dialog, or by the batch setting below: with
**Export JPEGs after each tray** on and **Keep raw scans after export** off, the app says
"Raw data is kept until you check and free the first tray. Later trays are freed after
verification." Turn **Keep raw scans after export** on if you want to keep every raw scan.

## 4. End of box or next section

- The toolbar has **Next section** and **Finish box**. Finish box advances the box number and
  starts a fresh holder (hands-free spec; **unverified** on real hardware).
- Do not mix boxes in one section. Check the box number and section name before the first
  scan of a new box.
- Before you leave a finished box: confirm every tray shows **Exported**, then check the output
  folder for the expected number of JPEGs.
- Do not delete raw scans until the box is exported and checked. Cleanup waits for your say-so.
- When you finish for the day: close the app (it restores the Eject watcher), then eject and
  take out the holder.

## 5. Troubleshooting

| Symptom | Likely cause | Do | Do NOT |
|---|---|---|---|
| Power light blinking | Scanner is starting up after power-on | Wait. A steady light means ready. | Send load or scan commands while it blinks. |
| Power light blinks for a very long time | **Unverified** | Switch OFF until the lights go out, then ON. Check the USB cable. | Keep pressing buttons. |
| Holder locked after a scan | By design: the holder stays latched until you eject (auto-eject not implemented) | Press **Eject holder** in the app, or the front-panel Eject button. Look at the holder. | Pull the holder out by force. |
| Load fails or stalls | Holder not at the stop, or a bad load. The driver then refuses all commands until a power cycle. | If the motor grinds or stalls: OFF at once (see Motor grinds). Otherwise close the app, run `of135i doctor` (section 6) while the scanner still responds, then power-cycle (section 2.1), reseat the holder, load once. | Retry load in a loop. Force the holder. |
| "Scanner is not loaded" after a power cycle | The scanner is cold | Do the cold start (2.1) and the in-app load step. | Press Scan before loading. |
| Scan interrupted (window closed, killed, Ctrl-C) | Scanner state unknown. The app records a marker and refuses scan, eject and load. | Power-cycle (2.1), load the holder, press **Confirm scanner reset…** (the primary button reads **Restart scanner…** in this state), rescan the tray. The tray is not enhanced or exported. | Delete the marker file by hand. Confirm before you have power-cycled and loaded. |
| Eject watcher conflict: the app refuses to open the scanner and tells you to stop the unit | The watcher unit, or a leftover `of135i watch`, is still alive. Only one program may use the scanner. | `systemctl --user stop opticfilm-eject-watch.service`, then open the batch again. | Kill `of135i watch` with `kill -9`. Use `pkill -f`. |
| The driver says "another program is using the scanner; wait for it to finish or close it, then try again" | Another `ofscan` or app run holds the scanner lock | Wait for it to finish or close it, then retry. | Start a second program on the scanner. |
| Slow step after the scan finishes | The driver's post-processing is about 2 min single-core per frame (one unit). The dust clean adds time (about 22 min vs 14 min per tray). | Wait. Stop other heavy jobs. | Close the app or power off during this step. |
| Slide rotates slowly in review | Preview was recomputed in about 19 s per slide (fix in progress) | Wait for the preview. Set orientation per batch where you can. Output is not affected: export recomputes from raw. | Rotate the same slide repeatedly while it works. |
| Motor grinds or stalls | Mechanical problem or a bad state | Power **OFF** at once. Send nothing else. Inspect the holder before you power on. `doctor` only helps before power-off, if the scanner still responds and it is safe (section 6). | Send more commands. Force the holder. Power on before inspecting the holder. |
| Calibration error "dark_b is residual" | The scanner is left in an unsafe state (reg 0x01 = 0x02). This is what the stop gate prevents. | Power-cycle, load, rescan. Keep the stop gate on. | Turn the gate off. Keep scanning without a power cycle. |
| App will not start | Missing PySide6 or Kirigami, wrong driver dir, or a second window holds the batch | Check the command in section 1 (`PYTHONPATH`, `OPTICFILM_DRIVER_DIR`). Install `python3-pyside6 kf6-kirigami qt6-qtdeclarative`. A second window on the same batch opens read-only. | Install the PySide6 wheel from PyPI (it cannot load system Kirigami). |

Messages the app shows (from `app/messages.py`):

- "Scanner is not loaded. Between trays, press Load next holder. After power-on, open
  'How to load the scanner' for the first terminal load." This text is older than the in-app
  load step. Follow section 2.1.
- "The scanner stopped this scan. Power-cycle it (off until the lights go out, then on), eject,
  load the holder again and rescan this tray."
- "Could not eject the holder." or "Could not load the next holder." Switch off until the lights
  go out, switch on, then load.
- "Holder: unknown after power-on or a fault; check by hand before scanning."

## 6. Quick reference

### Commands

```sh
# Status of the Eject watcher
systemctl --user status opticfilm-eject-watch.service

# Before any driver command from a terminal: CLOSE THE APP FIRST
# (the app polls doctor itself; do not run doctor while it is open)
systemctl --user stop opticfilm-eject-watch.service
# ... run the driver command ...
# Start the unit again only after the app is closed. The app stops it itself
# and refuses to open the scanner while a watcher is alive.
systemctl --user start opticfilm-eject-watch.service

# Read-only scanner state (app closed, watcher stopped)
cd <driver-dir> && PYTHONPATH=. python3 -m of135i doctor

# Is the scanner on the USB bus?
lsusb | grep 07b3
```

`<driver-dir>` is `<repo>/.local-research/publish/driver-latest`. Run `doctor` on the host
(`distrobox-host-exec` if you are in the container). The exact `doctor` invocation for the host
runner is **unverified**; the form above matches the load command the app prints.

### Files and folders

| What | Where |
|---|---|
| Driver logs | `~/.local/state/opticfilm-linux/logs` (or `$XDG_STATE_HOME/opticfilm-linux/logs`) |
| Interrupted marker | `~/.local/state/opticfilm-linux/scanner-interrupted.json` |
| Holder latch record | `~/.local/state/opticfilm-linux/holder-latch.json` |
| Scan scratch | `~/.cache/opticfilm-linux/work` |
| Batch folder | You choose it, **outside the repo**. Holds `batch.json` (the manifest) and the trays' raw scans. |
| Exported JPEGs | The export folder you pick. Heirloom layout: `box-<N>/<section>/<NNN>.jpg` |

- **`scanner-interrupted.json`**: a scan or holder move may not have finished. While it exists
  the app and `ofscan` refuse scan, eject and load. Clear it only with the app's **Confirm scanner
  reset…** button (also reached from the primary button **Restart scanner…**) after a power
  cycle and a fresh load. The terminal form `ofscan batch confirm-scanner --yes` needs the
  package installed; the production launch runs from `PYTHONPATH`, so `ofscan` is probably not
  on your PATH (**unverified**). A
  mark that cannot be read counts as present.
- **`holder-latch.json`**: what the app believes about the holder (latched, released,
  unknown). A new session starts as unknown and never restores the file, because the holder may
  change while the app is closed. After a known eject, Scan is refused until you confirm the
  holder is seated and latched.
- The Eject watcher unit is stopped while the app has a scanning batch open. "Inactive" is
  normal then. If the app was killed outright, start the unit by hand once the scanner is safe.

## 7. What is not verified yet

- **Cold first load through the finished app.** A prototype passed twice on 2026-11-02. The
  finished in-app flow still needs a run after a real power cycle (STATE.md: needs the owner).
- **First hands-free tray with auto export.** Auto export is off by default. A supervised tray
  with auto export on and keep raw on has not been run.
- **Flatpak.** The manifest is a draft and not buildable. Scanning from inside the sandbox is
  untested (`packaging/flatpak/README.md`).
- **Finish box / Next section** on real hardware, and the heirloom output layout on a real box.
- **Blinking power light that never stops** (section 5): cause unknown.
- **Light colour.** "Steady blue" is an owner observation, not in the repo.
- **Exact `doctor` invocation on the host runner** (section 6).
- **Speed figures** are from one unit: 14 min (`--no-clean`), 22 min (driver clean), 6 min
  (3600 dpi). Treat them as estimates.
- **Reliability.** Passing series so far are one unit, one operator. They are not a
  reliability figure.
- **Auto-eject** after a scan is not implemented, so every tray needs an eject action.
