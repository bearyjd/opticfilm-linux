---
title: "Scanner recovered from failed loads without a power cycle (one unit, 2026-11-01)"
author: Bearyjd
date: 2026-11-01
branch: main
cost: { minutes: 30, tokens: 69264 }
touches:
  - docs/ROADMAP.md
  - tests/0001.jpg
---

## Decided
2026-11-01, bare-metal `of135i load` (host python). Attempt 1: Enter pressed without taking the holder out -> StrictPollTimeoutError in load_magazine; the driver says only a power cycle can reset. Attempt 2 straight after, no power cycle: jog_magazine poll timeout (last f055, want f855). Attempt 3, holder properly reseated, still no power cycle: jog and load completed (status 0xdc55); `of135i doctor` read reg 0x01 = 0x22 idle-homed. The scanner recovered on its own.

## Tried and ruled out
Treating this as proof that a power cycle is never needed: it is one run on one unit, and all three attempts started from idle-homed (0x22), not cold. The driver's power-cycle message stays the rule.

## Watch out
Owner's question was whether recovery without a reset is possible: yes, observed once, after a failed load from idle-homed with the holder reseated. Run read-only `doctor` after a failed load before deciding; a failure mid-motion or from a cold state is a different case. The latch has no sensor: the owner confirms it by hand.
