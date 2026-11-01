# Roadmap

Where the project stands and what to do next. Written 2026-10-27. Update it as items finish.

## Where things stand

- The batch engine (scan, enhance, review, export, eject and load, scanner lock, interrupted-scan mark) is done and tested against the fake scanner.
- Real scanning has worked in limited tests on one unit: single slide at 600 dpi, all four slides at 600 dpi, single slide 1 at 3600 dpi, and (2026-10-31, one run, driver stop gate on) all four slides in one tray scan at 3600 dpi.
- Not verified: a second full four-slide tray at 3600 dpi (reliability), any full tray at 7200 dpi, and the native cold eject (the homing move has timed out twice).
- Nothing in the driver changes below has been run on the scanner.

## Next work, in order

### 1. Offline analysis of existing hardware logs (no hardware)

Decode the status values already recorded in the per-run logs and classify every calibration poll timeout and both cold-eject failures.

First results (2026-10-27), from the owner's local run logs (not published; unreviewed, treat as leads, not conclusions):

- One calibration read-back timeout per frame appears in **every** logged run, including the runs that finished cleanly (slide 1 at 3600 dpi, slide 4 at 600 dpi, all four slides at 600 dpi). It is not specific to slide 4 and is not by itself a failure signature.
- One older run shows a different pattern: the driver expected the scan engine running with data buffered and read it stopped with the buffer empty. It is the only logged case that matches the new calibration read-back guard.
- The slide-4 3600 dpi scan was stopped by hand at its second calibration read-back timeout, the same kind as in the clean runs. That abort was conservative, not a detected positioning failure. This does not show the 3600 dpi slide-4 scan would have completed.
- Both cold-eject failures ended on the same idle, sensor-clear status, not the exact value the driver waits for. The old logs have no read history, so they cannot tell "moved and stopped off the sensor" from "never started". The new outcome classification is intended to separate these on the next run; that is not yet confirmed.

Done: `scripts/poll_report.py` prints this classification for any run folder (see `docs/hardware-checklist.md`), so the next hardware session gets it automatically. Its output is a lead from logs, not proof.

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

The biggest unknown is the vendor's cold-start sequence. This is the evidence needed for a vendor-faithful cold start and eject. It is not risk-free: the vendor app has been seen to reposition a retained holder automatically at startup, so it needs the owner at the scanner with the physical state confirmed, like any other hardware step.

### 4. Driver changes to build after that, each opt-in until proven

Candidates from the local analysis. All are unconfirmed until the evidence above exists:

- Cold start and eject: a different move order, only if the capture confirms it.
- Calibration read-back: a different wait condition, only if real logs show the current one is wrong.
- Slide geometry: the native slide pitch may differ slightly from the vendor app's. Handle it in the empty-holder procedure.

### 5. More analysis (budgeted)

- Document the one startup branch that is still unexplained. No startup or cold-start guard is relaxed without hardware evidence.
- Compare the driver's motor and phase tables with the vendor app's. A mismatch might explain slow 3600 dpi scans. This is a question to check, not a finding.

### 6. Later

- Full four-slide at 3600 dpi, then 7200 dpi (needs about 9 GB free disk), only after items 2 to 4.
- A first-load button in the app, replacing the terminal `of135i load` step (seat the holder, then click Done).
- App restyle: rounded cards, pill buttons and segmented toggles, with dark mode as the default and a light mode option that is remembered between runs.
- App workflow and UX overhaul, designed together with the restyle above: fewer clicks per tray; multi-select slides (across trays) and apply a setting or an accept to all of them at once; one clear path from scan through review to export. Needs a short design pass (walk the real workflow with the owner) before any code.
- Image quality: (a) fix the lifted blacks in `auto_exposure` for linear positives (linear gain + sRGB curve instead of a median-targeting gamma); (b) optional deconvolution (Richardson-Lucy on luminance, off by default) as a pipeline option and CLI flag, UI toggle with the workflow overhaul; (c) 7200 dpi: a one-slide A/B on 2026-11-01 (slide 2, one unit) showed visibly finer grain, crisper edges and no blotchy artifacts versus 3600 dpi, at about 2.6x the time and about 5.4 GB peak memory per slide. Next: a full four-slide tray at 7200 dpi and an option in the app (owner's go for each hardware run). No AI upscaler on masters.
- Workflow: enhance and export one tray while the scanner scans the next (check whether the controller allows it today; holder moves and exports look exclusive). Saves the owner's waiting time, not scanner time.
- Scan speed (low priority, driver, motor-adjacent, owner's go per hardware run): at 7200 dpi a frame takes about 200 s, of which 175 s is the scan pass. Skipping the park between frames (about 8% of a tray), calibrating once per tray (about 3%) and a smaller overscan (at most 7%) would save about 15-18% together. No single-pass slide profile exists (slide scans are dual-light only).
- Bulk selection everywhere (owner, 2026-11-01): click to select, Ctrl-A select all, Shift-click for ranges, Ctrl-click to toggle, then apply an edit or action to the whole selection. Fewest possible clicks for the basic workflow (load, scan, auto-export).
- Hands-free scan to JPEG (design: docs/superpowers/specs/2026-11-01-hands-free-scan-to-jpeg-design.md): auto enhance, accept, export and free raw data per tray; at the end only the JPEGs are left.
- Carousels (owner, 140 slots, many empty): one section per carousel; compact numbering; optional per-slide carousel slot in the metadata; skip empty frames inside a holder if the driver accepts non-consecutive slide lists (unchecked).
- Say why an export wrote nothing: when every accepted slide is already exported, the dialog should say "all N slides already exported; change a setting to export again" instead of "0 files exported". Offer an explicit "re-export" action in the new workflow.
- Research contributing the driver to SANE (sane-backends): current support for the 135i (`07b3:1436`), their contribution rules, and licensing and credit relative to the community driver. Backends are C, so this would be a port, not a submission. Research only until the owner decides; nothing goes upstream without the owner's say-so.
- Make the Flatpak manifest buildable (real hashes and pins, desktop file, metainfo, icon).
- A sanitised protocol write-up for the driver fork, and coordination with the upstream driver author on four-slide support. Nothing goes upstream without the owner's say-so.

## Rules that stay in force

- No timeout is lengthened, and no presence or start-state guard is loosened, to make a scan pass.
- New motor-affecting behaviour ships opt-in and off by default, with the default wire traffic unchanged.
- Hardware work needs the owner at the scanner and a confirmed physical state.
- No vendor binaries, disassembly, configuration, captures or images are published.
