# Hardware checklist (before any real scan)

Run through this before the first scanner command of a session, and again after any failed or
interrupted scan. Evidence behind it comes from tests on a single unit; treat it as cautious
practice, not a guarantee.

1. **Confirm the physical state.** Look at the scanner: is a holder inside, partly inside, or
   out? Do not assume from the last command's output; the word "ejected" is not proof of release.
2. **Power-cycle.** Switch the scanner off and leave it off until its lights go out, then switch
   it on and wait for the steady light.
3. **Clear the tray.** Only if the holder is confirmed released, remove it and check nothing is
   left in the slot. If it seems retained, do not force it.
4. **Front-load a fresh holder.** Insert the holder from the front without force, and confirm by
   hand that it is seated and latched.
5. **No commands until the holder is seated.** Send nothing to the scanner (no load, scan, eject,
   or status command) until step 4 is done.

If anything looks wrong while the motor runs (grinding, holder not moving, unexpected noise),
send nothing further and switch the scanner off. Do not force the holder; if it will not come out
by hand, stop and get advice. Then start again from step 1.

This sequence is not a validated recovery procedure. Earlier successful runs also used the
driver's full `load` step (jog, reinsert, hand-confirm the latch) after seating the holder; follow
the driver's own prompts for that step.

## Reading poll timeouts after a run (offline)

After a hardware session, summarise the poll timeouts recorded in the run folders. This only reads
files; it never touches the scanner.

```sh
uv run python scripts/poll_report.py RUN_DIR [RUN_DIR ...]
```

It reads `*.log`, `*.diag.json` and `eject*.log`, and prints, per run, calibration read-back poll
timeouts (each classed `benign-baseline`, `acquisition-not-observed`, `other` or `malformed`) and
cold-start move timeouts (last and wanted status word, decoded bits, writes, execute pulses, and the
outcome word when the log has one). A run with no outcome word cannot tell a move that started and
stopped from one that never started. Each run ends with one `VERDICT` line.

Treat the output as a lead from logs, not proof. The exit code is 0 whatever it finds, and 2 only
when a folder cannot be read. Use `scripts/check_spike.py` for the pass/fail verdict.

## Hardware ladder for off-by-default switches

`OPTICFILM_SKIP_EMPTY`, `OPTICFILM_SKIP_GAPS`, `OPTICFILM_AUTO_EJECT` and
`OPTICFILM_LOAD_AND_SCAN` (and the saved "Load & scan" setting) are built and tested on the fake
driver only. Leave each off until the rungs mapped to it below have passed.

Standing rules for every rung:

- The owner is at the scanner and gives the go for each rung. Stop at the first anomaly.
- Run `systemctl --user stop opticfilm-eject-watch.service` before the rung and restart it after.
- Never use `pkill` or `pgrep -f`.
- **Abort rule:** if the motor grinds or stalls, switch the scanner off, then run a read-only
  `of135i doctor` before anything else.
- Use the driver fork checkout the app uses (`OPTICFILM_DRIVER_DIR`). Write batches outside the repo.
- Keep each trace in `.omc/hw/skip-eject-<date>/rN/`.
- "Passed" for a rung that gates a default means it passed twice, on separate sessions.
- Run H7 last: it ends with the power-cycle procedure above.

| Rung | Command / action | Pass | Purpose |
|---|---|---|---|
| H0 | `of135i doctor` (read-only) | idle-homed, holder loaded | baseline |
| H1 | CLI `scan --slides 1-3 --dpi 600 --ir -o ...` with 3 slides loaded, opening 4 empty | 3 frame sets f1-f3, no f4, exit 0, idle-homed after | consecutive subset |
| H1b | CLI `scan --slides 1-4 --dpi 600 --ir` (same session, same holder) | 4 frame sets, exit 0 | reference geometry for H2 and H3 |
| H2 | CLI `scan --slides 1,3 --dpi 600 --ir` | f1 and f3 only; the f3 crop matches H1b's f3 within 0.2 mm; exit 0 | non-consecutive openings |
| H3 | CLI `scan --slides 2-4 --dpi 600 --ir` | first frame is not 1; geometry within 0.2 mm of H1b | positioning from home for a later first frame |
| H4 | App, `OPTICFILM_SKIP_EMPTY=1`, 600 dpi batch, "3 slides" | tray shows 3 slides and an empty tile; export numbering has the gap | app path for skipping |
| H5 | App, `OPTICFILM_AUTO_EJECT=1`, 600 dpi full tray | eject 1-2 s after the scan, latch "ejected", then load-next works | automatic eject |
| H6 | Only if the driver's own `scan --eject` is wanted: CLI `scan --slides 1-4 --dpi 600 --ir --eject` | prints `ejected (eject move completed...)`; record whether `eject NOT run` appears | evidence for the driver eject |
| H7 | App, AUTO_EJECT on, press Stop during frame 2 | NO eject; tray FAILED/interrupted; marker set; then the power-cycle procedure | an abort never ejects |
| H8 | 7200 dpi, app, SKIP_EMPTY "3 slides" plus AUTO_EJECT, hands-free with auto export | about 640 s scan, eject, JPEGs 001-003, raw freed after verification | production shape |

### Which rungs unlock which switch

| Switch | Rungs that must pass first |
|---|---|
| `OPTICFILM_SKIP_EMPTY` (openings 1..N) | H0, H1, H1b, H4 |
| `OPTICFILM_SKIP_GAPS` (any set of openings; needs SKIP_EMPTY) | the SKIP_EMPTY rungs, plus H2 and H3 |
| `OPTICFILM_AUTO_EJECT` | H5, H7 and H8, each passed twice (H8 also uses SKIP_EMPTY) |
| `OPTICFILM_LOAD_AND_SCAN` / "Load & scan" | no rung written yet; it relies on the eject, so at least H5, plus a supervised one-press load-and-scan run to be added to this ladder |

The owner then decides the defaults. The first one to turn on would be `OPTICFILM_AUTO_EJECT`.

### Results

Copy a row per attempt.

| Date | Rung | Session | Result (pass/fail) | Trace folder | Notes |
|---|---|---|---|---|---|
| | | | | | |
