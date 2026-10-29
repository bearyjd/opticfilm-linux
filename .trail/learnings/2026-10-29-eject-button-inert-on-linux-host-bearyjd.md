---
title: "Eject button inert on Linux: host must poll EP 0x83"
author: Bearyjd
date: 2026-10-29
branch: main
cost: { minutes: 3, tokens: 70873 }
---

## Decided
- The front-panel eject button only emits one byte (0x48) on interrupt EP 0x83 after init; nothing moves the motor by itself. Only `of135i watch` (cli.py ~3303) polls and ejects, so scans/loads/idle app ignore the button. Fix direction: app runs the watch poll in the background (small app-side PR).
- Gate on/off image comparison (spike/b-8o9QyWxX b5 vs b6/b7): difference is a 1-2 count shadow offset out of 255 (gate off: frame1 ~4.1/7.3/4.0 RGB vs frames 2-4 ~2.9/4.9/2.8; gate on: uniform ~3.3-3.6/6.1-6.5/3.3-3.6). Not visible at thumbnail size.

## Tried and ruled out
- Judging gate quality by eye on thumbnails: useless, quantify shadow mean/percentile per frame instead.

## Watch out
- EP 0x83 can sit in EOVERFLOW after a load that did not drain it (InterruptOverflowError); no button events until power cycle. Diagnose with `of135i buttons` / `doctor` (read-only) before assuming a code bug.
- Hardware items (slide4@3600 gated, arm C, readback-guard fail run) need the user's "go" and a known scanner state; default-gate PR waits for that evidence.
