---
title: "App Eject-button handling: design, review fixes, and what is still unverified"
author: Bearyjd
date: 2026-10-29
branch: docs/hardware-day-2026-10-28
cost: { minutes: 2, tokens: 17079 }
touches:
  - README.md
---

## Decided
- PR #32 (merged, 65a1a6f): the front-panel Eject button ejects the holder, ON by default, kill switch OPTICFILM_EJECT_BUTTON=0/false/no/off (anything else, including unset or garbage, leaves it on). It reuses the existing status poller (doctor's `== Button ==` section) and the existing eject_holder path; no new motor path.
- Rules that each have a test which fails when the guard is removed: act only when idle and the poller's own current status is READY or NOT_READY; drop (never queue) presses while busy, scanning or read-only; the first reading after attach or after any busy/error poll only sets a baseline (a stale press never ejects); a held press ejects once (rising edge); an unconsumed press expires after PRESS_TTL_S = 2.0 s; a press reported with ERROR, needs-power-cycle or a refused engine state is ignored; a failed eject is never retried.
- The driver status and the button reading are returned together (`StatusReading`, `DriverScanner.status_reading()`), not through shared mutable state.

## Tried and ruled out
- Making it opt-in: the user wanted it to start with the app, so default-on plus a kill switch is the decision.
- A separate `eject_pressed()` call after `status()`: reviewers found a race on shared `_button` and a hole where an ERROR reading still counted a press. Merging the two into one reading removed both.
- A kill switch that only treated the exact string "0" as off failed open for "false" or "off"; both reviewers flagged it.

## Watch out
- Not yet run on hardware in the app. The driver's `watch` path was verified (one press, one eject). Still to try with a loaded holder: press Eject idle (expect one eject) and press it during a scan (expect nothing).
- Driver status is cached up to 3 s; a very short tap between two real `doctor` calls could be missed if the press does not stay queued on EP 0x83. Observations so far suggest it does stay queued until read, but that is inferred.
- A press with no holder (NOT_READY) still sends an eject, matching the UI button. A late reading arriving after the poller closes can still record a press; nothing reads it (cleanup follow-up).
