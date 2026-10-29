---
title: Owner ran the app Eject-button handling on hardware (2026-10-28 night)
author: Bearyjd
date: 2026-10-29
branch: docs/eject-button-hardware-reported
cost: { minutes: 7, tokens: 293218 }
touches:
  - docs/CONTRIBUTING.md
  - README.md
  - docs/CODEMAPS/app.md
  - docs/CODEMAPS/architecture.md
  - docs/CODEMAPS/batch-pipeline.md
  - docs/CODEMAPS/scanner.md
  - ".omc/STATE.md"
---

## Decided
- The owner reports they ran the app's Eject-button handling (PR #32) on the real scanner on the night of 2026-10-28. Treat the app path as run on one unit. No logs of that run were captured in this repo, so the details (what was pressed, whether the press-during-scan case was tried) are not recorded.
- README now says that, and that the do-nothing cases (press during a scan, held press) are covered by fake-scanner tests only.

## Tried and ruled out
- Earlier notes (including the 2026-10-29 eject-button design note) say the app path was not yet run on hardware. That was true when written in that session and is superseded by the owner's report.

## Watch out
- Next hardware session: capture a driver trace for an app-driven button eject and for a press during a scan, so the claim has a log behind it.
