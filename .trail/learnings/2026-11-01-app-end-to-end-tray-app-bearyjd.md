---
title: App end-to-end tray + app Eject button verified on hardware (2026-11-01)
author: Bearyjd
date: 2026-11-01
branch: main
cost: { minutes: 23, tokens: 68840 }
touches:
  - docs/ROADMAP.md
---

## Decided
2026-11-01 the owner ran the app on the host (bare-metal python, pyusb + tifffile via pip --user): first load from a terminal, a 4-slide 3600 dpi tray (~6 min, all frames poll_timeouts=0), accept all, export 4 slides, then the app's Eject holder button ejected the holder. Eject worked (owner-reported, logs not read). Host-runner eject-watch check also PASSED (scanner-off and --live).

## Tried and ruled out
Launching the app without OPTICFILM_DRIVER_RUNNER=host: the shown load command then uses distrobox `dev`. The owner wants it to not depend on distrobox.

## Watch out
After a power cycle the scanner is cold and the app says "not loaded" until a terminal `of135i load` is done. The app steps the eject-watch unit aside while a batch is open (inactive is normal). Still unverified: unit restart after the batch closes, exit-75, probe, LED, bare-metal load.
