# Hardware day 2026-10-28/29: combined review of the native calibration runs

Status: analysis of recorded diagnostics only. Nothing here was re-run on hardware. Labels: PROVEN (directly in the recorded data), SUGGESTED (consistent, not isolated), UNPROVEN.
Method: the repo's offline checkers (`scripts/check_spike.py`, `scripts/poll_report.py`) on each run folder, plus aggregation of the per-frame `diag.json` files. "Gate" is the opt-in wait for the scanner to report that the previous calibration acquisition has stopped before the next one is armed.

## Runs

| Run | dpi | Slides | Gate | Driver revision with integrity fields |
|---|---|---|---|---|
| b1 | 600 | 4 | off | no |
| b2 | 600 | 1-4 | off | no |
| b3a | 3600 | 1 | off | no |
| b3b | 3600 | 4 | off | no |
| b4, b5 | 600 | 1-4 | off | yes |
| b6, b7 | 600 | 1-4 | on | yes |
| b8 | 3600 | 1 | on | yes |
| c2 | 3600 | 4 | on | yes |
| c3 | 600 | 4 | on + buffer-empty wait (arm C) | yes |
| c4 | 600 | 4 | on + read-back guard set to fail | yes |

## Offline checker verdicts

| Run | check_spike | Note |
|---|---|---|
| b1 | PASS | passes although the dark_b block is a residual (see below); the checker has no stale-read test |
| b2, b3a, b4 | FAIL | more than one poll timeout in some frame, and a calibration read-back warning |
| b3b | PASS | clean-looking |
| b5 | FAIL | partly an artefact: the run folder holds no driver log and no recorded exit code; frame 2 has 2 real poll timeouts |
| b6, b7, b8 | PASS | |
| c1 folder (c2/c3/c4) | FAIL | artefact: no driver log in the folder; the three runs share one folder and are aggregated as one. Images, park and poll timeouts are fine |

`poll_report` classifies the b4/b5 timeouts as benign-baseline even though every b4/b5 frame is marked degraded by the driver. Neither tool can see a stale read; only the driver's `cal_integrity` field does.

## Per-frame aggregate (diag.json)

| Run | Frames | cal_integrity | Dark_b fallback used | dark_b substituted | Poll timeouts per frame | Offset codes (R,G,B) |
|---|---|---|---|---|---|---|
| b1, b2, b3a | 6 | n/a (older revision) | default offset codes | False | 1-2 | 267,266,267 |
| b3b | 1 | n/a (older revision) | none | False | 0 | 266,265,266 |
| b4, b5 | 8 | degraded x8 | default offset codes | False | 1-2 | 267,266,267 |
| b6, b7 | 8 | clean x8 | none | False | 0 | 266,265,266 |
| b8, c2, c3, c4 | 4 | clean x4 | none | False | 0 | 266,265,266 |

Degraded reasons in all 8 b4/b5 frames: calibration dark_b read judged stale (re-armed 5.8-9.3 ms after the previous clear while the previous acquisition still read as running; data arrived 2.3-4.1 ms after the execute pulse), a stale gain-check read (7 frames gain_check_a, 1 frame gain_check_b), and the dark_b residual fallback. The two stale phases are the same in every off frame, so the behaviour is deterministic within a run. Gate-on reasons: none.

Dark means (16-bit counts) and a-vs-b drift:

| Group | dark_a means (R,G,B) | dark_b means | b minus a |
|---|---|---|---|
| Residual frames (b1, b2, b3a, b4, b5) | 20.3-22.2k, 22.5-24.6k, 21.3-22.9k | 25.6-26.8k, all three channels equal | 4.6-5.4k, 2.2-3.2k, 3.9-4.4k |
| Healthy frames (b3b, b6, b7, b8, c2, c3, c4) | 20.5-22.4k, 22.7-24.9k, 21.5-23.0k | distinct per channel, 23.0-27.4k | 2.4-2.5k in every channel, every frame |

dark_b with identical channel means is the signature of a residual (stale) block. dark_a level follows session time rather than the gate: the gate-on runs c3/c4 have the same dark_a as gate-off b4/b5.

Gain codes drift by about 3 counts between runs (R 41-44, G 32-34, B 38-39) with no relation to the gate. The `cr_mismatches` counter is 35-59 per frame in every run, gate on or off; it behaves like a noise floor and is not a gate signal.

Scan-stop wait (gate-on runs, 7 waits per frame, 12 frames, 84 recorded waits): outcome `clear` 84/84, no timeouts.

| Run | wait_s min / median / max | since_clear_s min / median / max |
|---|---|---|
| b6 | 0.0049 / 0.0149 / 0.0234 | 0.0121 / 0.0167 / 0.0281 |
| b7 | 0.0051 / 0.0145 / 0.0243 | 0.0132 / 0.0164 / 0.0278 |
| b8 (3600) | 0.0006 / 0.0134 / 0.0144 | 0.0123 / 0.0146 / 0.1592 |
| c2 (3600) | 0.0011 / 0.0098 / 0.0148 | 0.0111 / 0.0169 / 0.1606 |
| c3 | 0.0008 / 0.0137 / 0.0157 | 0.0144 / 0.0167 / 0.0236 |
| c4 | 0.0055 / 0.0146 / 0.0203 | 0.0157 / 0.0166 / 0.0263 |

A minimum wait under 1 ms means the scanner had already stopped, so the wait did not block in every phase. The ~0.16 s since_clear maxima occur only in the two 3600 dpi runs.

GO-to-data per calibration acquisition (7 per frame; the multi-second maximum is the shading step):

| Group | Minimum | Median | Stale-suspect acquisitions per frame |
|---|---|---|---|
| Gate off (b4, b5) | 2.3-2.6 ms | 31-34 ms | 2 (frame b4-1: 2 plus one not-observed); 8 of 8 frames affected |
| Gate on (b6, b7, b8, c2, c3, c4) | 30.1-33.6 ms | 33-37 ms | 0; 12 of 12 frames clean |

A 2-3 ms GO-to-data cannot be a fresh exposure, which is why those reads are judged stale. With the gate on, every acquisition takes tens of milliseconds, the range expected for a real exposure. Re-arm gap after the previous clear for the two suspect phases: 5.8-12 ms gate off, 19-31 ms gate on. Total calibration time at 600 dpi: 2.8-3.2 s off, 2.7 s on; at 3600 dpi about 5.1 s on (no gate-off 3600 run exists on the newer revision).

Image shadow statistics (600 dpi, darkest-20% mean, from the session notes): gate off about 4.0/7.3/4.0 (b4) and 2.9-4.1 / 4.9-7.3 / 2.8-4.0 (b5, inconsistent between frames); gate on 3.3-3.6 / 6.1-6.5 / 3.3-3.6, uniform. Column streaking identical in all runs. The difference is 1-2 counts of 255.

## Gate on vs off: valid and invalid comparisons

- Valid: b4/b5 (off) vs b6/b7 (on): same dpi, slides, flags, driver revision.
- Not comparable: b1-b3b lack the integrity fields. For them "residual" is inferred from identical dark_b channel means, the fallback tag and poll timeouts.
- No valid pair at 3600 dpi: b3a/b3b are off on the older revision, b8/c2 are on with the newer one.
- Run order is not interleaved: all gate-off runs precede all gate-on runs (b5 ended about an hour before b6 started on the next UTC day). Lamp warm-up and power state between them were not controlled.
- Frames within a run share their calibration environment; the effective sample is runs (6 off, 6 on), not frames.

Results that complicate the "gate fixes it" story:
1. b3b (gate off, 3600 dpi, slide 4) looks clean: distinct dark_b channels, no timeouts, no fallback. b3a, 90 s earlier at the same dpi and gate setting, did not. The race is therefore not deterministic across runs; a gate-off run can pass by luck.
2. Offset codes are not "the same in all runs": they differ by 1 count between fallback frames and clean frames. That is the fallback itself, not independent evidence. Among clean frames they are identical across dpi, slides and runs, so the offset step shows no run-to-run information.
3. `dark_b_substituted` is False in every frame, including all degraded ones. The substitution path was never exercised; degraded frames used the fallback offsets.
4. The shadow-statistics difference is 1-2/255 and is at least partly explained by the 1-count offset difference, and is confounded with run order.

## PROVEN

- Gate off, 600 dpi, newer revision: 8/8 frames degraded, two calibration reads served 2-4 ms after execute, residual dark_b, default-offset fallback, 1-2 poll timeouts per frame.
- Gate on: 12/12 frames clean, 0 poll timeouts, 0 stale suspects, every GO-to-data at least 30 ms.
- The scan-stop wait cleared 84/84 times, median about 15 ms, no timeouts.
- The residual signature appears in every pre-gate 600 dpi run (slides 1-4, about 12 h apart).
- The offline checkers do not detect stale reads (b1 passes; b5 is called benign).

## SUGGESTED

- The gate causes the improvement (confounded by order; b3b shows luck can mimic it).
- The shadow improvement comes from the offset difference rather than from the dark data directly.
- Arm C engaged in c3: the poll records show the buffer-empty acceptance rule replacing the data-enable rule on all 7 waits.
- 3600 dpi is not itself a risk factor (b3b clean without the gate, b8/c2 clean with it).

## UNPROVEN

- That the gate, rather than run order, warm-up or power state, caused the change between b4/b5 and b6/b7. No interleaved A/B exists.
- The gate-off failure rate at 3600 dpi on the newer revision.
- Arm C: the diagnostics have no per-wait record for the buffer-empty wait (no duration or outcome). c3 also had the scan-stop gate on, so its own effect cannot be separated; c3 matches c2/b6 (median GO-to-data 35 ms vs 34-37 ms).
- The guard-fail path: c4 produced a clean frame and exited normally with 50 cr_mismatches, so the guard never fired and its trip condition is unknown. No degraded state was present to test it.
- The substitution path (never seen).
- Why healthy dark_b is about 2.5k above dark_a in every channel of every healthy frame, and whether that is correct.
- Any real image-quality gain beyond a 1-2/255 shadow shift.
- What `cr_mismatches` measures.

## Cheapest next experiments, ranked

Common flags (from the recorded `cli` block): `--slides N --dpi D --ir --no-clean --overscan 1.5 --dark-fallback default`. Gate flag name as planned: `--cal-wait-scanstop off|enforce` (confirm with `--help`). One load and one power-on per series.

| # | Experiment | Flags per scan | Resolves |
|---|---|---|---|
| 1 | Interleaved A/B, 3 pairs: off, on, off, on, off, on | `--slides 4 --dpi 600` with `--cal-wait-scanstop off` / `enforce` alternating | order and warm-up confound |
| 2 | Gate off at 3600 dpi, slide 4 and slide 1, 3 each | `--dpi 3600 --slides 4` and `--slides 1`, `--cal-wait-scanstop off` | is b3b luck; 3600 gate-off rate on the newer revision |
| 3 | Guard exercise in a state that degrades | `--dpi 600 --slides 4 --cal-wait-scanstop off --cal-readback-guard fail`, expect a FAIL exit on a degraded frame; repeat once if it comes out clean | guard-fail path |
| 4 | Arm C isolated, after adding a per-wait diagnostic record (no hardware needed for that change) | `--cal-wait-scanstop off --cal-wait-bufempty` vs `--cal-wait-scanstop enforce`, 600 dpi, 2 each | does C alone prevent stale reads |
| 5 | Offline: re-derive integrity for b1-b3b from the saved calibration data; recompute shadow statistics with the offset code as covariate | none (no hardware) | closes the old-revision gap; tests the offset explanation |
| 6 | Warm-up control: first scan right after lamp-on vs after 20 min | `--cal-wait-scanstop enforce --dpi 600 --slides 4` | warm-up vs gate |

Scrub checklist (verified by grep on this file): no hex register values, no `decoded-`, no `~/captures`, no `.pcap`, no `disasm`, no symbol names, no vendor timing numbers, no image or raw-byte content.
