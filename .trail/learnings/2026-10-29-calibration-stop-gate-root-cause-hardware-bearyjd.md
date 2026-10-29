---
title: "Calibration stop gate: root cause, hardware evidence, and what was not proven"
author: Bearyjd
date: 2026-10-29
branch: docs/hardware-day-2026-10-28
cost: { minutes: 52, tokens: 82658 }
touches:
  - spike/logs/eject-doctor-pre.log
  - spike/logs/c1-buttons-1.log
  - ".local-research/session-2026-10-28.md"
  - spike/logs/c1-watch-1.log
---

## Decided
- Root cause (one scanner): the native firmware re-armed calibration 5.8-9.3 ms after the SCAN-clear while the previous acquisition still ran, giving a stale dark_b and a default-offset fallback. The driver's opt-in `--cal-wait-scanstop enforce` waits for the stop before the next transfer.
- Evidence, 600 dpi tray: gate off 8/8 frames degraded; gate on 8/8 clean (two runs). Gate on, slide 4 at 3600 dpi (previously hand-aborted): exit 0 in 86 s, finished TIFFs, cal_integrity clean, dark_b_substituted False, all scanstop waits "clear" (1-15 ms), no poll timeouts. Slide 1 at 3600 also clean. Five gated runs, zero gate-caused failures.
- Image difference gate off vs on is a 1-2 count (of 255) shadow offset: judge it by per-frame shadow mean/percentile, not by eye on thumbnails.
- The default-on change is a separate draft PR in the driver fork (branch feat/cal-wait-scanstop-default, local until approved). It must stay a draft until the evidence above is accepted; a gate failure fails the session.

## Tried and ruled out
- Reading the "want ec55/e855" vs "fc55/f855" poll-warning values as proof that `--cal-wait-bufempty` engaged: WRONG. The same ec55/e855 appears without that flag at 600 dpi; the value follows resolution. Arm C (`--cal-wait-bufempty enforce`, 600 dpi) only proved it does not break a scan; the diag has no per-wait record for it. Add one before claiming it works.
- `--cal-readback-guard fail` ran clean at 600 dpi, but nothing triggered it, so the failure path is still untested on hardware.
- slide4@3600 without the gate (earlier run) also showed no dark_b substitution, so 3600 dpi alone does not show a gate-on/off contrast; the 600 dpi runs carry that evidence.

## Watch out
- Opt-in flags stay opt-in and motor-adjacent changes need the user's explicit "go" per hardware run, with the user watching; power off only on grinding/stalling or a driver-reported failure.
- The same two poll-timeout warnings (dc55/d855) appear in every clean run: benign baseline, not a reason to abort.
