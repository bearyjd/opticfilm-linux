---
title: Default resolution for new batches is now 7200 dpi (owner decision 2026-11-01)
author: Bearyjd
date: 2026-11-01
branch: docs/ux-design-and-plan
cost: { minutes: 30, tokens: 63269 }
touches:
  - docs/ROADMAP.md
  - docs/superpowers/specs/2026-11-01-hands-free-scan-to-jpeg-design.md
  - docs/superpowers/specs/2026-11-01-ux-workflow-plan.md
---

## Decided
2026-11-01 the owner chose 7200 dpi as the default for NEW batches (G3), after an aligned same-area comparison (slide 2, same tone curve and JPEG q95) showed 7200 is visibly cleaner than 3600 even when shrunk to 3600 size: finer grain, crisper edges, and none of the blocky blotches present in the raw 3600 data. Cost: about 4x JPEG size, about 2.4x scan time (207 s vs 75-90 s per slide), about 5.4 GB peak per slide. A four-slide 7200 tray is clean on this unit (827 s, poll_timeouts=0), also while a 7200 export runs.

## Tried and ruled out
Keeping 3600 as the default (the Oct 25 and Oct 29 learnings): they say design for 3600 or higher and that real scans will be at 3600; 7200 is the "higher" they allow for. Old manifests keep their stored dpi; only new batches default to 7200.

## Watch out
JPEG size (full vs 3600-size) is a per-batch setting, default full. The 7200 path through the APP (not the driver CLI) was not run on hardware at the time of writing.
