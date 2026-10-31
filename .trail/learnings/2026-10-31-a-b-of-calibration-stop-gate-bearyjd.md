---
title: "A/B of calibration stop gate: 3 vs 3, gate on is clean"
author: Bearyjd
date: 2026-10-31
branch: docs/ab-gate-result-2026-10-30
cost: { minutes: 18, tokens: 75029 }
touches:
  - ".local-research/tools/ab_gate_test.sh"
  - spike/logs/b-ab-2026-10-30-ab-warmup-enforce.log
---

## Decided
Interleaved A/B on 2026-10-30 (600 dpi, slide 4, one scanner, order warmup(on) 1(off) 2(on) 3(on) 4(off) 5(off) 6(on)): gate off was degraded 3/3 (runs 1, 4, 5), gate on was clean 3/3 (runs 2, 3, 6; warmup also clean). Every degraded run showed the stale-read signature (calibration re-armed 5.7-11.9 ms after the previous clear while the acquisition still read as running) plus the default_offset_codes dark_b fallback. Gate-on runs had 7 stop-gate waits and no poll timeouts. This supports the gate being the default. Data: spike/b-ab-2026-10-30 (local, not published).

## Tried and ruled out
The earlier confound (all gate-off runs came before gate-on) is gone: off-runs 4 and 5 came after on-runs 2 and 3 and still degraded, so it is not warm-up or drift. Not tested: other dpi, other slides, other scanners; cal_bufempty_waits was empty because that flag was not enabled (arm C engagement still unproven).

## Watch out
Run scripts that import the driver inside the distrobox dev container (host python has no pyusb: ModuleNotFoundError 'usb'): distrobox enter dev -- bash -lc 'cd <repo> && .local-research/tools/ab_gate_test.sh <dir>'. Three runs per arm is a sample of runs, not frames; do not claim more than one scanner at 600 dpi.
