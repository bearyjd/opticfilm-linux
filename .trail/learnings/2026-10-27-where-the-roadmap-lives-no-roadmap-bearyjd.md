---
title: Where the roadmap lives (no ROADMAP file)
author: Bearyjd
date: 2026-10-27
branch: main
cost: { minutes: 1, tokens: 57372 }
touches:
  - ".omc/STATE.md"
  - ".omc/plans/four-slide-run-2026-10-25.md"
  - docs/superpowers/specs/2026-10-21-plustek-slide-scanner-design.md
---

## Decided
- There is no ROADMAP.md. For "what's next", read .omc/STATE.md (<=60 lines), .omc/plans/four-slide-run-2026-10-25.md, the Phases section of docs/superpowers/specs/2026-10-21-plustek-slide-scanner-design.md, and the README status block.
- Next phase is supervised hardware gates (user at the scanner, G0 power-cycle and front-load first), then the full 4-slide scan at 3600 dpi, then 7200 dpi.

## Tried and ruled out
- grep for "roadmap" only hits the spec's mention of the upstream driver's roadmap (item C2); it is not this repo's roadmap.

## Watch out
- Set OPTICFILM_DRIVER_DIR to the latest driver checkout (.local-research/publish/eject-outcomes) before hardware runs; the app does not switch drivers itself.
- Open failures are slide 4 at 3600 dpi (calibration warnings, scan interrupted) and cold-eject homing timeouts. No evidence supports loosening checks or timeouts.
