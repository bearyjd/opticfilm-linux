---
title: "SANE load miss: poll-cadence blind A/B (4 ms vs 20 ms) and what it did and did not show"
author: Bearyjd
date: 2026-11-07
branch: docs/learning-resume-2026-11-04
cost: { minutes: 42, tokens: 138695 }
touches:
  - ".omc/STATE.md"
  - ".omc/upstream-pr-drafts/issue50-ir3600-reply-DRAFT.md"
  - ".omc/hw/poll20-experiment-runsheet.md"
  - ".omc/hw/capture-2026-11-06/r1-vs-r3-cadence.md"
  - ".omc/hw/capture-2026-11-06/r1-vs-r3-analysis.md"
  - ".omc/upstream-pr-drafts/issue50-ir3600-reply-skeleton.md"
---

## Decided
2026-11-07: the SANE backend polls status every 4 ms (poll_interval_ms in backend/genesys/gl126_ops.h) while the Python driver polls about every 20 ms during homing moves (offline R1 vs R3 capture comparison, .omc/hw/capture-2026-11-06/r1-vs-r3-cadence.md). A 20 ms build was made (branch ot135i-poll-cadence-experiment, scratch-poll20) and tested in a blind 10-run A/B on the owner's unit (.omc/hw/poll20-ab/, mapping revealed after run 10): the 20 ms build loaded 5/5, v9 loaded 4/5. The only miss and the only stutter were the same v9 run (op 24 PollMasked timeout, first 0xfc last 0xfc, the known bit 0x08 signature). No statistically meaningful difference. The owner's own judgement is that the 20 ms build is much better than the default; the owner declined further tests.
## Tried and ruled out
Edge-wait length as a predictor of the miss (loaded runs ranged 2.7 s to 32 s, the misses sat in the middle). Weak push as the main cause (the owner may have loaded weaker on a few runs that still loaded and sounded normal). A settle-time (25 present reads) A/B: set up then cancelled by the owner, leaving a local worktree A-settle25 and branch ot135i-settle-experiment, nothing pushed. Overall v9 miss rate on this unit is about 3 in 10 loads.
## Watch out
Never tell the maintainer the 20 ms build is proven better; say what the data shows and quote the owner's impression as theirs. The sound notes asked about "the load", but the measured cadence difference is in the homing rounds, so a sound benefit there was not captured. After a failed load the scanner is cold and --eject-film is refused ('Press Load film, its jog is the release'); the next load's jog frees the tray. Reading run logs reveals the build (poll counts), so blind tests must not open them until done.
