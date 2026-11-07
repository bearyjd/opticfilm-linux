---
title: "SANE load sound: stuttery at session start, normal later on the SAME v9 build (warm-up confound)"
author: Bearyjd
date: 2026-11-07
branch: docs/learning-resume-2026-11-04
cost: { minutes: 3, tokens: 20557 }
---

## Decided
2026-11-07, owner's observation: at the start of the hardware runbook (the IR 3600 session, build scratch-log = v9 + logging, same code as blind build Y) everything sounded stuttery, the load included, and the first load missed. Later in the same session the blind A/B runs sounded normal or Python-like on both builds, including v9 on runs 3, 4, 7 and 9. So the same unchanged v9 code went from stuttery to normal; the sound followed the unit's condition and the load outcome, not the code. The 20 ms build cannot be credited with the improvement. Run 1 ("sounded great", 20 ms build) was the first load after the stuttery start and is confounded by the unit smoothing out.
## Tried and ruled out
Attributing the better sound to poll_interval_ms 4 -> 20: the builds differ clearly in the logs (cold-start status reads about 420 vs 95 per step, same wall time) but the owner could not hear a build difference in the 10 blind runs, and v9 also sounded Python-like. Warm-up alone as the whole story: v9 run 5 stuttered and missed about 55 minutes into the session.
## Watch out
Order effects: a session that starts with a cold, long-idle unit makes the early runs sound worse; randomise or warm the unit up with a few throwaway loads before judging sound. Do not claim a code improvement from sound notes taken across a warm-up. The owner declined further tests.
