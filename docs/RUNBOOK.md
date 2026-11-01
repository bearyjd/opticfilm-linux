# Linux host operations runbook

<!-- AUTO-GENERATED -->
<!-- Sources: README.md, docs/eject-watch-service.md, docs/hardware-checklist.md, docs/performance.md, docs/CONTRIBUTING.md, scripts/hardware/README.md and scripts/eject-watch-check.sh; includes the operator-reported observation below (2026-11-01). -->

This is a local Linux scanner application. Deployment procedures and HTTP health endpoints:
**none**. Check the desktop app, scanner's physical state, driver logs and optional systemd user
unit instead. See [environment configuration](ENV.md) and the
[Eject-watcher reference](eject-watch-service.md) for configuration and service details.

## Safety and startup

Real hardware runs require continuous physical supervision. If the motor grinds, stalls, the
holder stops moving unexpectedly, or there is unexpected noise, **switch the scanner OFF
immediately** and send no further commands. Do not force a retained holder; stop and get advice.
This emergency OFF takes priority over diagnostic capture.

1. Inspect the actual holder position; output saying “ejected” does not prove release. Follow
   the [hardware checklist](hardware-checklist.md). For a normal session start, switch OFF until
   the lights go out, then ON and wait for the steady light. After a failure, capture the state
   before that power cycle as described under recovery, when safe.
2. Stop the watcher before terminal driver commands:
   `systemctl --user stop opticfilm-eject-watch.service`. Only one program may own the scanner.
3. Front-load the holder without force and follow the driver's full first-load prompts. Run
   `of135i load` **yourself in a real terminal, never through an agent or an IDE's `!` prefix**.
   Use the command shown by the app's **How to load the scanner** dialog: it selects the configured
   host or distrobox runner. When prompted, pull the holder fully out, reinsert it to the stop,
   press Enter, and confirm by hand that it is latched. Perform one full `load` per power-on
   series; do not retry it repeatedly in a failed session.
4. Start `opticfilm` (or `scripts/opticfilm-here` for the development-container checkout), open
   or create the batch, then scan. The app cannot perform the interactive first load. Its help
   dialog can be opened beforehand to copy the command.

Between trays, use **Eject holder**, confirm physical release, then swap holders. **Load next
holder** uses hardware-unproven `load --next-strip` and remains disabled unless explicitly
enabled; see [ENV.md](ENV.md). Do not infer that it is a proven replacement for first load.

## Watcher operations and checks

The optional user unit is `opticfilm-eject-watch.service`. Inspect it with:

```sh
systemctl --user status opticfilm-eject-watch.service
journalctl --user -u opticfilm-eject-watch.service --since "-5min"
```

Before opening a scanner-capable batch, the app stops this unit. It restores it on batch close
or app exit only if it had been running before. If stopping fails or its resulting state cannot
be established, the app refuses to open the scanner. An app killed outright cannot restore the
watcher; start it manually once the scanner is safe and idle. Never use `kill -9` on `of135i
watch`; its stop must use SIGINT. See the [service reference](eject-watch-service.md).

Run the one-command check from a host terminal, choosing the actual physical state:

```sh
# Scanner OFF, lights out: start path and stop from failed/restarting state
scripts/eject-watch-check.sh /path/to/opticfilm135i-linux
# Scanner ON and idle-homed: observed live watcher must survive 10 s, then stop cleanly
scripts/eject-watch-check.sh --live /path/to/opticfilm135i-linux
```

Do not press Eject during `--live`. The default mode does not prove a live SIGINT stop.
`--runner auto|host|distrobox[:NAME]` selects the runner; the driver folder defaults to
`OPTICFILM_DRIVER_DIR`. Optional `--enable` enables the unit only after PASS and never starts it.
The check installs/validates the unit and starts/stops it; it does not simulate a button press.

| Exit | Meaning |
|---|---|
| 0 | PASS |
| 1 | FAIL |
| 2 | Usage error |
| 3 | Refused: scanner state wrong or unknown |
| 4 | Refused: an `of135i watch` already runs or `/proc` cannot be read |
| 5 | INCONCLUSIVE |

Real-system evidence is limited: distrobox passed scanner-OFF and live checks on one system on
2026-10-31. Host-runner behavior remains unverified on real hardware; offline tests use stubs.
The service reference records further limits of watcher identity checks.

## Failure recovery

When safe, record the failed scanner state with the read-only **`of135i doctor` before any
power cycle**. The supervised `scripts/hardware/hw_run.sh` helper automatically captures it
after a failed run, with a 30-second timeout. Doctor failure does not change the helper result;
the helper records driver/tee statuses in its log and returns success only when both succeeded
(`scripts/hardware/hw_run.sh:33-44`).
Use the configured driver environment and stop the watcher first. Do not delay an emergency OFF
to run diagnostics; after an abnormal motor event, send nothing further until the checklist's
physical recovery sequence is complete.

The driver recovery rule remains: a failed session requires a power cycle. Power-on resets the
scanner to cold, never-homed state and destroys the previous failure state. Inspect the holder,
switch OFF until the lights go out, switch ON, follow the checklist and first-load prompts,
then rescan the tray. After an interrupted scan or holder move, clear the batch interlock only
after physically resetting and loading: `ofscan batch confirm-scanner --yes`, or **Confirm
scanner reset…** in the app. Interrupted trays are not enhanced or exported.

**Single observation, 2026-11-01:** the operator reported recovery after reseating a holder
without a power cycle. This is one observation supplied for this runbook, not independently
verified repository evidence or a validated recovery procedure. It does not change the driver's
power-cycle rule.

After a failed/interrupted watcher eject, exit 75 keeps the unit stopped. Following safe state
capture, physical inspection and power-cycle recovery, use `systemctl --user reset-failed
opticfilm-eject-watch.service`, then start the unit when ready. This exit-75 behavior has only
been tested with the fake device; propagation through distrobox/podman is unverified.

## Common problems

| Symptom | Operator action |
|---|---|
| “Scanner is not loaded” | Open **How to load the scanner** and complete the first load in a real terminal. If this follows a failed session, follow recovery first; do not loop `load`. |
| Poll timeout | Preserve logs and, when safe, the pre-cycle `doctor` state. Run `uv run python scripts/poll_report.py RUN_DIR [RUN_DIR ...]` offline. Its classifications are leads, not proof; exit 0 is not a pass verdict (2 means an unreadable folder). Use `scripts/check_spike.py` for the offline pass/fail verdict. |
| Missing PySide6 or Kirigami | Install the distribution packages; the PyPI PySide6 wheel bundles Qt that cannot load system Kirigami. Fedora packages: `python3-pyside6 kf6-kirigami qt6-qtdeclarative`. Use an environment with system site packages; see [Contributing](CONTRIBUTING.md). `ofscan` needs neither GUI package. |
| Host driver cannot import dependencies | Host Python needs `pyusb`; hardware helpers also need `tifffile`, directly or through `OF135I_DEPS_OVERLAY`. Check the interpreter and dependency location in [ENV.md](ENV.md). |
| Load command uses the wrong environment | `OPTICFILM_DRIVER_RUNNER=host` selects direct execution; unset uses distrobox `dev`, and `distrobox:<name>` chooses another container. The watcher's installer independently defaults to `auto`; inspect its selected runner rather than assuming both match. |
| “another program is using the scanner” | Close the other app/CLI owner and inspect the watcher unit. Stop the watcher before terminal driver work. |

## Resolution and storage budget

These are **fake-driver rehearsals**, not real scan timings. The 2026-10-29 profile on a
22-core, 62 GB machine under other load measured:

| DPI | Script total, all stages | Batch enhance, four slides | Export, four TIFF16s | Peak anonymous / RSS |
|---|---|---|---|---|
| 3600 | 41.5 s | 8.9 s | 6.7 s | 0.66 / 1.73 GB |
| 7200 | 194.5 s | 42.1 s | 31.7 s | 2.40 / 5.40 GB |

Script totals include standalone one-slide work; real scanner time is set by the hardware.
A busier 7200 dpi repeat took 233 s total and 68 s batch enhancement. A full real tray at
7200 dpi remains untested. See [performance measurements](performance.md) for stage definitions.

Raw sweeps occupy about 0.87 GB per tray at 3600 dpi and 3.5 GB at 7200; four exported TIFFs
were about 0.45 GB and 1.8 GB respectively in the 2026-10-22 rehearsal. Measured folder disk
peaks were 2.1 GB and 8.6 GB, excluding a temporary unlinked stitch memmap needing another
0.87 GB or 3.5 GB until saved. The rehearsal guidance calls for about 9 GB free at 7200 dpi;
allow additional headroom for that excluded allocation and retained batches. Keep scan scratch
on disk, away from RAM-backed `/tmp`. Raw scans persist for reprocessing until explicit cleanup
of an exported tray; exporting recomputes from those raws.

<!-- /AUTO-GENERATED -->
