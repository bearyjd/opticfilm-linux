---
title: "Slide production 2026-11-07: app path, measured timings, dust flag, review lessons"
author: Bearyjd
date: 2026-11-08
branch: main
cost: { minutes: 26, tokens: 560734 }
touches:
  - ".omc/plans/slide-production-runbook-2026-11-07.md"
  - ".omc/STATE.md"
  - ".omc/plans/overnight-2026-11-08-pipeline.md"
  - docs/PRODUCTION.md
  - ".omc/plans/hardware-runbook-2026-11-07.md"
  - ".omc/hw/slide-b1-session-2026-11-07.md"
---

## Decided
Production runs through the APP (driver-latest `scan --slides`, AutoPipeline, GUI / `ofscan batch run`), NOT the `--holder slide` CLI used for the upstream PRs #73-75. 3600 dpi for the whole run (owner: never lower, 7200 too slow); raw not kept; keep-raw stays ON until two supervised trays pass. Defaults PR #135, `--skip` #134, pre-flight #133, averaging/timing tools #132, guess assist #137, bulk metadata scopes #136 (entire-batch default + auto JPEG update). Owner's eye: the dust filter works well on real slides (dust-on JPEGs good).
## Tried and ruled out
Direct hop between openings (FEEDL is relative from home, calibration measures at home), whole-tray sweep (net zero: scans the gaps), --overscan 0.5 (saves ~2 s/slide), 7200 dpi (4x scan time, 0.5-0.7x noise, no real detail beyond 3600), my own tray-loop script (duplicated `ofscan batch run`; closed #130). The app's `--slides` path does NOT calibrate once per tray for dual/IR: ~5.2 s on EVERY opening (measured).
## Watch out
Measured on one unit: scan pass 44 s/opening at 3600, ~73 s/slide wall via `--slides`, app enhance+export 50-70 s per tray (scanner-limited). All real slides flag DUST (mask 3-4% vs 0.5% threshold) and that HOLDS the tray (contradicts spec goal 4). Pilot frames from the CLI with --slide-positive are TURNED; the app expects 5824x5184 native frames. Independent reviewers found real bugs in every PR (path overwrite, wrong lock probe, receipt crash-recovery, race); keep the review step. CI Qt abort came from a test helper walking the QML tree; find items by objectName. merge_when_green adds merge commits: fetch+merge before pushing fixes, never force-push.
