---
title: "Arm C aborts alone: bufempty budget 0.3 s vs ~1 s; full tray at 3600 dpi clean"
author: Bearyjd
date: 2026-10-31
branch: docs/tray-3600-and-armc-2026-10-31
cost: { minutes: 30, tokens: 423311 }
touches:
  - docs/eject-watch-service.md
  - tests/test_eject_watch_yield.py
  - src/opticfilm_linux/app/controller.py
  - packaging/systemd/opticfilm-eject-watch.service
  - src/opticfilm_linux/app/eject_watch.py
  - src/opticfilm_linux/app/status.py
  - README.md
  - src/opticfilm_linux/app/qt/app.py
  - tests/test_app_holder_export_lock.py
  - src/opticfilm_linux/app/holder.py
---

## Decided
2026-10-31: all four slides at 3600 dpi in one tray scan with the stop gate on were clean (one unit, one run, ~296 s). `--cal-wait-bufempty enforce` with the stop gate off aborted in cal_dark_b after the poll's 0.3 s budget (status cc55, bit 0x40 still set; healthy runs show ad55 ~1.0-1.4 s after the execute write), and the scanner needed a power cycle. Owner chose option B: leave arm C off and unfixed; the stop gate already prevents the stale-read signature.

## Tried and ruled out
Raising the 0.3 s budget (option A: separate ~2-3 s budget for the bufempty poll) was not done: it would be a new timeout on a motor-adjacent flag and needs a driver PR plus another hardware run. Reading the abort as a stale read is wrong: the status word stayed well-formed.

## Watch out
Any arm C re-run needs the scanner power-cycled first and a fresh `load` in a real terminal. Two "poll timed out, continuing" warnings appeared in the tray log but not in per-frame diag counts, so poll_timeouts=0 does not mean no warnings.
