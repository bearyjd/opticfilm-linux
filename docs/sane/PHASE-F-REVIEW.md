# Phase F: pre-submission review of the local GL126 backend branch (2026-11-03)

Local only. Target: `ot135i-feasibility` in the local sane-backends clone (now one commit `d12ffce25` on SANE master `f4bd07353`; the model is registered only behind `SANE_GENESYS_ENABLE_EXPERIMENTAL_GL126`, default off). Nothing was pushed or sent anywhere. Reviewer: Opus (architect, read-only), then fixes by Codex, offline checks re-run.

## Verdict
**Fix first, then proceed to hardware bring-up (D1).** No BLOCK. The default-off switch holds: the model is registered only under the macro, `get_matching_usb_dev` throws for 07b3:1436, and the default-build test asserts `sane_init`/`sane_open` return INVAL. No generic Genesys path can issue a motor command a Python reference run would not; shared-file hunks affect only GL126 except two behaviour-neutral changes (option-index check before the busy check; `ScannerInterfaceUsb` now calls its USB object through a reference).

## Findings and resolution
| # | Finding | Resolution |
|---|---|---|
| H1 | A scan failure was only remembered in-process (`dev->gl126_session_failed`); a later `scanimage` could pass `load_document` on the persisted `loaded` mark and drive the motor with no power cycle | `latch_failed_session()` also writes the persistent `MagazineMarkKind::Failed` mark; only a cold register 0x01 read clears it. New tests: calibration transport failure, incomplete close, failed `end_scan` with pass pending, fresh-process refusal, both `sane_start` exception branches |
| H2 | A stray session file was committed (`backend/.omc/...`) | Removed; `.omc/` excluded; no `.omc` path tracked |
| H3 | Test file header claimed another author's copyright (copied from GL128 tests) | New GL126 test/converter/README headers: "Copyright (C) 2026 bearyjd and contributors", GPL-2.0-or-later |
| H4 | Commits authored with a private email; no credit to the original author | Re-authored to the GitHub noreply address; squashed; credit line added ("Based on the opticfilm135i-linux driver by Christian Gillinger") |
| M1 | Headers differ from the PROVENANCE inventory (Voltz 2010-2016, Kanapickas 2019 notices) | They come from the GL124 files the GL126 code derives from; documented in comments, the manual and the commit message |
| M3 | Generated tables reference `tools/gen_sane_tables.py`, which is not in the tree or pinned | **Open for submission**: pin the generator (commit/hash) or include it; a licensing question for the "preferred form for modification" |
| M4 | Magazine mark trusted any owner | Validated: regular file, current uid, single link; tests added |
| M5 | `sane_cancel` (signal context) could run `end_scan` concurrently with a read | Lock-free atomic cancel flags only; the reader honours them at transfer boundaries and persists failure outside the handler; SIGUSR1 interrupt tests added |
| LOW | frame range 1-6 vs 4; "seen on hardware" comments; sensor/cmd-set entries compiled in default builds; no conf entry | Frame range 1-4 everywhere (v1 scope); comments say "from captures of the Python driver, not validated through SANE"; unreachability documented and tested; `genesys.conf.in` omission is intentional |

## Still open
- Submission blockers independent of code quality: decisions D-2 and D-3 (contacting the original author; SANE linking exception, which only the copyright holder can add).
- M3 above.
- Not determined by the review: whether the Python reference keeps failure state across processes or what real hardware reads after these failures (if it reads something other than 0x22/0x00, H1's severity drops); strict-poll details inside `run_phase_program`; the contents of the 25k-line generated tables; real-hardware behaviour of cancel during motion.
- Offline results after the fixes: GCC no-USB, GCC USB + XML replay and Clang passed, with the documented existing Clang C warning in an unrelated file. No hardware was used.

## Appendix: fix report (verbatim from the fixing run)
GL126 pre-submission review fixes — 2026-11-03

Completed locally on `ot135i-feasibility`. HEAD remains
`a21a2309b040e054f5c9130e24c1a686daaae185`. All changes are uncommitted.
No network, push, commit, remote changes, master checkout, hardware access,
USB discovery, or external messages were used. `$S` was unset, so this report
and `sane-commit-msg.txt` are in the clone root, within the requested workspace.

Implemented findings:

- **H1:** `latch_failed_session()` sets the terminal session/pass state and
  writes `MagazineMarkKind::Failed`. SessionFailGuard, close, end_scan refusals,
  and generic open/start/read exception paths use it. A warm register 0x01
  value of 0x22 cannot clear the persisted failure or reuse a Loaded mark.
  The existing cold-read recovery rule remains in place; the in-process
  terminal latch still requires a backend restart.
- **H1 regressions:** cover calibration transport failure, incomplete close,
  failed end_scan with a pass and pending tail present, fresh warm device
  initialization, and real sane_exit/init/open process-state reset followed
  by a warm start refusal. Lifecycle tests inject both SaneException IO_ERROR
  and an active-pass SaneException INVAL, exercising both sane_start catch
  branches. Existing runtime_error injections remain.
- **H2:** removed the committed advisory JSON with `git rm --cached` and added
  `.omc/` to `.git/info/exclude`. No `.omc` path is tracked. The requested
  removal is staged; implementation changes remain unstaged. The ignored
  local advisory file is retained, as expected for `--cached`.
- **H3/M2:** all requested GL126 test, converter, and fixture README headers
  now say `Copyright (C) 2026 bearyjd and contributors` and identify
  `GPL-2.0-or-later`. Original GL128 files and their copyright are unchanged.
- **M1:** source comments, the manual, and the commit-message draft explain
  that the Voltz 2010-2016 and Kanapickas 2019 notices credit the GL124 files
  from which gl126.cpp/.h/_registers.h derive, alongside Christian Gillinger's
  2026 copyright. GPL-2.0-or-later notices have no linking exception. Added:
  “Based on the opticfilm135i-linux driver by Christian Gillinger (cgillinger).”
- **M4:** magazine_mark_read validates descriptor metadata before reading:
  regular file, current uid ownership, and exactly one hard link. Tests cover
  an actual owned mark, FIFO, directory, and symlink. Foreign ownership is
  tested using synthetic st_uid metadata through the exact production
  predicate; no privileged chown or foreign-owned fixture was used.
- **M5:** scan-pass state is device-owned. Both GL126 sane_cancel paths only
  use lock-free atomic flags, before logging or exception wrappers; they do
  not traverse maps, mutate pass state, take a mutex, or issue USB transfers.
  The reader checks cancellation at transfer boundaries and persists failure
  outside the signal handler. Actual SIGUSR1 tests interrupt a descriptor OUT
  and final bulk-IN, then verify terminal failure and write-free retry/cleanup.
  The reader drains/PARKs a healthy pass synchronously at EOF and retires
  scanner/read flags. Only successful PARK publishes safe completion, so
  ordinary scanimage post-EOF cancellation preserves Loaded authorization.
  A positive regression runs the real PARK program with scripted settled
  replies, checks AlreadyParked, cancel/close, and successful warm reuse.
- **LOW:** kFrameMax and kFeedlFrameMax both permit only frames 1–4, with
  agreement and boundary tests. Six-frame donor geometry remains unchanged
  as provenance, with comments distinguishing it from v1 scope. Capture
  comments now identify the Python driver and lack of SANE validation.
  Sensor/command-set comments explain default-build unreachability; existing
  absent-model and named-mock-open tests still run. genesys.conf.in explains
  the intentional omission of an OpticFilm 135i discovery entry.

The final offline matrix uses `.phase-fix/run-checks.sh`, adapted only to put
logs under `.phase-fix/evidence/` from the existing Phase D script. Each mode
rebuilt the backend and ran Genesys unit tests, four safe SANEI tests, the
sane-desc checks, and the mocked scanimage cancellation test. Stock USB
discovery and saned/socket tests remain excluded, as in Phase D.

| Configuration | Result | Evidence |
| --- | --- | --- |
| GCC no USB, experimental registration enabled, C/C++ -Werror | PASS; 5,584 assertions, zero failures | `fix-nousb-enabled.log`, `unit-nousb-enabled.log` |
| GCC USB + XML, experimental registration enabled, C/C++ -Werror | PASS, including the five XML replay programs | `fix-usb-enabled.log` |
| GCC USB + XML, default registration disabled, C/C++ -Werror | PASS; 3,774 assertions, zero failures | `fix-usb-default.log`, `unit-usb-default.log` |
| Clang, default registration disabled, C++ -Werror | PASS; 3,744 assertions, zero failures | `fix-clang-default.log`, `unit-clang-default.log` |
| Distribution archive and selected-file audit | PASS; 48 relevant files present and byte-identical | `dist.log`, `dist-audit.json` |

Clang C retains Phase D's `-Werror -Wno-error=gnu-folding-constant` exception
for unchanged dll.c; this is not a strict whole-tree Clang C -Werror claim.
GCC USB uses `-std=gnu11`, as before. The USB build is left with registration
disabled. No toolchain installation, download, or autoreconf was needed.
Distribution creation used a fresh directory and disabled Automake's recursive
removal commands. Its generated changes to 26 translation files were restored
to their initial HEAD bytes; the generated patch is retained as evidence.

Four negative controls in `.phase-fix/mutate-guards.py` separately disabled
failure-mark persistence, uid checking, the frame bound, and signal cancellation.
All triggered test failures. The signal mutant first failed the cancellation
assertions and then terminated on SIGUSR1; this is recorded as exit -10, not a
passing test. Every source mutation was restored. The rebuilt restored suite
passed with 5,584 assertions and zero failures, and the 48-file archive audit
again confirmed identical source bytes. See `mutation-summary.log` and
`mutation-*-{build,tests}.log`.

A separate reviewer approved the final code and independently ran the restored
no-USB enabled, USB default, and Clang default binaries: respectively
5,584 / 3,774 / 3,744 assertions, all with zero failures and exit 0. Logs are
`independent-*.log`; review evidence is `independent-review.md`.
`git diff --check` passes. `final-state.json` records the branch, HEAD, source
hashes, staged deletion, and absence of tracked .omc paths. The initial
`.gitignore` change and pre-existing scratch artifacts were preserved.

These are offline results. Synthetic image tests and scripted PARK replies
do not establish hardware calibration, timing, or motion safety through SANE.
The existing lack of a full captured image-payload replay remains unchanged.
