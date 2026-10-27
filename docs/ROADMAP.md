# Roadmap

Where the project stands and what to do next. Written 2026-10-27. Update it as items finish.

## Where things stand

- The batch engine (scan, enhance, review, export, eject and load, scanner lock, interrupted-scan mark) is done and tested against the fake scanner.
- Real scanning has worked in limited tests on one unit: single slide at 600 dpi, all four slides at 600 dpi, single slide 1 at 3600 dpi.
- Not verified: the full four-slide tray at 3600 dpi, any full tray at 7200 dpi, and the native cold eject (the homing move has timed out twice).
- Nothing in the driver changes below has been run on the scanner.

## Next work, in order

### 1. Offline analysis of existing hardware logs (no hardware)

Decode the status values already recorded in the per-run logs and classify every calibration poll timeout and both cold-eject failures.

First results (2026-10-27):

- The calibration read-back timeout seen on slide 4 (last value `ec55`, wanted `dc55`) appears in **every** run, including the runs that finished cleanly (slide 1 at 3600 dpi, slide 4 at 600 dpi, all four slides at 600 dpi). It is not specific to slide 4 and is not by itself a failure signature.
- The earlier `cc55` vs `ad55` divergence is different: the driver wanted the engine running with data buffered and read the engine stopped with the buffer empty. It appears in only one older run. It is the only recorded case that matches the new calibration read-back guard.
- The slide-4 3600 dpi scan was stopped by hand at its second `ec55`/`dc55` timeout, which is the same value pair as in the clean runs. That abort was conservative, not a detected positioning failure.
- Both cold-eject failures ended at the same status value, the one that means idle on the sensor-clear side, not the exact value the driver waits for. The old logs have no read history, so they cannot tell "moved and stopped off the sensor" from "never started". The new outcome classification will answer this on the next run.

Still to do: a short report script under `scripts/` that prints this classification for any run folder, so the next hardware session gets it automatically.

### 2. One supervised hardware session

Follow `docs/hardware-checklist.md`. Stop at the first anomaly and never retry automatically.

1. Read-only reads first (status registers after a failure). Some reads may change device state, so this is the owner's call.
2. Slide 4 at 600 dpi with the new log-mode diagnostics on. Judge the run with `scripts/check_spike.py`.
3. Then the opt-in driver flags, one at a time, each compared with the log-mode baseline:
   - `--position-require-motor-idle`
   - `--cold-move-early-fail`
4. The calibration guard stays in `log` mode until real logs justify `fail`.

Set `OPTICFILM_DRIVER_DIR` to a fresh checkout of the driver fork's master first. The app does not switch drivers by itself.

### 3. Capture of the vendor app from power-on with a retained holder

The biggest unknown is the vendor's cold-start sequence. This capture is passive on the scanner side and is the evidence needed for a vendor-faithful cold start and eject.

### 4. Driver changes to build after that, each opt-in until proven

- Cold start and eject: replace the nine forward moves with a reverse-to-sensor, then forward sequence, once the capture confirms it.
- Calibration read-back: wait on the data-enable status as the vendor does.
- Slide geometry: the native slide pitch (62.6 mm) differs slightly from the vendor setting (62.53 mm), about 0.21 mm of drift by slide 4. Handle it in the empty-holder procedure.

### 5. More analysis (budgeted)

- Narrow the blanket startup refusal by mapping the one startup branch that is still unexplained.
- Compare the driver's motor and phase tables with the vendor's constants. A mismatch could explain slow 3600 dpi scans.

### 6. Later

- Full four-slide at 3600 dpi, then 7200 dpi (needs about 9 GB free disk), only after items 2 to 4.
- Make the Flatpak manifest buildable (real hashes and pins, desktop file, metainfo, icon).
- A sanitised protocol write-up for the driver fork, and coordination with the upstream driver author on four-slide support. Nothing goes upstream without the owner's say-so.

## Rules that stay in force

- No timeout is lengthened, and no presence or start-state guard is loosened, to make a scan pass.
- New motor-affecting behaviour ships opt-in and off by default, with the default wire traffic unchanged.
- Hardware work needs the owner at the scanner and a confirmed physical state.
- No vendor binaries, disassembly, configuration, captures or images are published.
