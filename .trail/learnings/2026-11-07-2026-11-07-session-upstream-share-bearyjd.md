---
title: "2026-11-07 session: upstream share, Load & scan merged, slide branches built, runbook ready"
author: Bearyjd
date: 2026-11-07
branch: docs/learning-resume-2026-11-04
cost: { minutes: 7, tokens: 35462 }
touches:
  - ".omc/OVERNIGHT-REPORT-2026-11-07.md"
  - ".omc/STATE.md"
  - ".omc/plans/upstream-asks-2026-11-07.md"
  - ".omc/plans/hardware-runbook-2026-11-07.md"
  - ".omc/hw/session-s1-2026-11-06.md"
  - ".omc/upstream-pr-drafts/slide-pr1-dust-dpi-scaling.md"
  - ".omc/upstream-pr-drafts/slide-pr2-slide-geometry.md"
  - ".omc/upstream-pr-drafts/slide-pr-cli.md"
---

## Decided
Owner said "share all upstream": opened upstream PRs #66 (dust dpi, merged by the maintainer) and #67 (slide geometry model, library only), each after an independent review. The maintainer's rest-of-C2 request (issue #70) was accepted; slide branches b1-b3 are built and reviewed in the fork but are NOT opened upstream until a hardware run on the owner's unit. Load & scan core (#122) and UI (#123) merged with the setting OFF by default (two reviews each; a junk OPTICFILM_LOAD_AND_SCAN now forces OFF). Fork synced with upstream master (fork #28), flatpak pin bumped (#125). Hardware runbook: .omc/plans/hardware-runbook-2026-11-07.md; the SANE logging build is .local-research/sane-v9-build/scratch-log (env block taken from .omc/hw/session-s1-2026-11-06.md).
## Tried and ruled out
Committing replay-harness fixtures with bulk-OUT payloads: shading tables derive from calibration frames, so the branch was rewritten with lengths only (old SHAs stay fetchable on GitHub until GC). Squashing review fixes into the slide branches: new commits and merges instead, because squashing means a force-push. Tightening the slide crop for a 37 mm aperture with an edge band: impossible without also refusing the real 35.06 mm mount, so the guarantee is stated as apertures up to 36 mm.
## Watch out
Branches under .local-research/publish/ are separate clones: `origin` there is the maintainer's upstream, the fork remote is `fork`, so always push to `fork`. CI is slow (about 10 minutes), so use background waits; a foreground wait over 600 s gets moved to the background. Renovate #63 (CI container fedora:43 to 46) is green but unmerged, waiting on the owner.
