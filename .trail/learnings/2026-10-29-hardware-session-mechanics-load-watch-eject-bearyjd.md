---
title: "Hardware session mechanics: load, watch, eject, and test environment gotchas"
author: Bearyjd
date: 2026-10-29
branch: docs/hardware-day-2026-10-28
cost: { minutes: 1, tokens: 3397 }
touches:
  - ".local-research/tools/hw_run.sh"
---

## Decided
- Eject button works on hardware through the driver: `of135i watch` read the press and ejected (log: "eject button pressed", "ejected (eject move completed: status word read idle)"). The press shows as one `eject` event; a read-only `of135i buttons` run saw it too. Later `doctor` runs read `idle`, so the read appears to consume the queued event; an unread press waited long enough to be reported by a later `doctor`. Inferred from two observations, not proven. The app-side feature is PR #32 and has NOT been run on hardware yet.
- Hardware run helper: `.local-research/tools/hw_run.sh <attempt_dir> <name> <of135i args>`. A gated scan: `scan --slide 4 --dpi 600 --ir --no-clean --dark-fallback default --cal-wait-scanstop enforce -o <dir>/tray.tiff`.
- Stop `of135i watch` only with SIGINT (Ctrl-C equivalent) after an eject has finished; it never ends by itself. Never `timeout`-kill it: a kill during an eject could land mid-move.

## Tried and ruled out
- Running the interactive `load` through the Claude Code `!` prefix: it exited 130 at the "Press Enter" prompt after 5 s (no usable stdin) and demanded a power cycle. Run `load` in a real terminal.
- `pgrep -f` / `pkill -f` in this repo's shell: a hook blocks it (it matches its own shell). Use `ps -eo pid,args | grep "[o]f135i watch"` and a captured PID.
- Running driver tests with plain python: tifffile is missing. Use PYTHONPATH="<checkout>:<root>/.local-research/publish/deps-overlay" (1191 pass on the draft gate branch). For the app in a git worktree use the root `.venv/bin/python` with PYTHONPATH=src (1993 pass, 97% coverage on the eject-button branch).

## Watch out
- Load needs the holder fully out and then seated at the stop before Enter; the driver only senses presence, so check latching by hand. After the load the scanner reads status 0xdc55 (reg 0x01 = 0x22).
- The driver writes calibration buffers to a relative `1/` folder in the working directory (the stray untracked `1/` in the repo root).
- A previous eject-button learning says the button is inert on Linux: that is true only while nothing polls EP 0x83. This note supersedes the "never works" reading.
