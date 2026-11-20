---
title: "Tray scan time goes to the driver's per-opening cycle, and the command line scan has no faster mode to copy"
author: Bearyjd
date: 2026-11-20
branch: perf/tray-budget-tool
cost: { minutes: 21, tokens: 259708 }
id: l-456afcc1
scope: repo
status: active
src: agent
touches:
  - scripts/tools/tray_budget.py
  - tests/test_tray_budget.py
---

## Decided
2026-11-20, first supervised tray (4 openings, 3 slides, 3600 dpi, 314 s wall): about 175 s scan passes, 36 s parking, 27 s positioning, 21 s calibration (about 5 s on every opening) and 35 s between openings. The empty fourth opening cost about 85 s, and the pause between load finishing and the scan starting was 72 s (a person pressing the button). The fork's slide scan loop and upstream's holder-slide loop do the same serial cycle for every opening, so the command line holds no faster mode the app lacks. The only CLI-side extra is the post-scan overlap thread in a closed fork PR. Cheap wins that already exist: scan only the openings that hold slides, Load & scan, and auto-eject (the last two never run on hardware). Next measured candidates, all needing supervised hardware runs: drop the replayed vendor idle pauses at 3600 dpi (about 6 s per opening), overlap post-processing, reuse calibration after the first opening.

## Tried and ruled out
Skipping the park between openings (a direct hop): feed distance is forward-only from the park position, there is no position readback or reverse command, and slide 4 already ends near the travel limit. It needs a new motor primitive and hardware experiments, so no code was written.

## Watch out
The docs disagree on whether the feed distance is absolute or relative; the device comment (relative to where the carriage is, which works only because park returns it to one place) is the precise one. Dual/IR calibration reruns on every opening because no vendor capture of a skipped one exists, not because it was proven impossible. Full app suite takes about 40 minutes and flakes in the eject-watch tests when the machine load is high; rerun those alone before blaming a change.

In the code:
- scripts/tools/tray_budget.py splits a run's wall time from the kept per-frame diag sidecars
- driver: of135i/device.py _scan_dual (cache cleared, recalibrates), park around line 4153, PREP replay ops in tables_ir
