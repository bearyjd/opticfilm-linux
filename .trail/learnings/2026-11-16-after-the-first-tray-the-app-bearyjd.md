---
title: After the first tray the app got recovery fixes and a smarter new-batch form; the lost-contact cause is still unknown
author: Bearyjd
date: 2026-11-16
branch: docs/trail-learning-tray1-fixes-20261116
cost: { minutes: 75, tokens: 98733 }
id: l-32a1f08d
scope: repo
status: active
src: agent
touches:
  - tests/test_app_close_anyway.py
  - src/opticfilm_linux/batch/batch_name.py
  - tests/test_batch_name.py
  - docs/PRODUCTION.md
  - src/opticfilm_linux/app/qml/StartPage.qml
  - tests/qt/test_start_page_prefill.py
  - src/opticfilm_linux/app/prefs.py
  - tests/test_app_prefs.py
  - src/opticfilm_linux/app/qt/start_bridge.py
  - docs/RUNBOOK.md
---

## Decided
2026-11-16, PRs #154 (recovery) and #155 (new-batch prefill) merged after independent Opus reviews. The owner's "lost contact" left no log and no data loss (batch.json: tray 1 exported); #154 makes the next one diagnosable and survivable: bounded wait after SIGKILL, "Close anyway" in the window-close dialog, a banner saying accepted slides are saved with a Close batch button, and `~/.local/state/opticfilm-linux/logs/doctor-last-failure.log` (one file, written only for a new failure, not for scanner-off). #155 prefills Name (first phrase of the guess box), Collection, Album, Section, Slides per section and Box = last + 1, never over typed values.
In the code: scanner/driver.py `_stop_group` / `abort_running` / `_keep_doctor_failure`, app/controller.py `close_anyway`, app/qml/Main.qml, ErrorBanner.qml, batch/batch_name.py, app/prefs.py `remember_layout`.

## Tried and ruled out
Salvaging finished frames of a failed multi-frame scan: not needed for this incident (the failing attempt never created a tray). Ending a Name at every period or slash: gave "St", "Mt", folder "74" for "Christmas 12/25/74"; now only a period before a capital after a non-abbreviation ends it, and "/" becomes a space. A Name starting with dots could become the parent folder; leading dots are stripped.

## Watch out
The owner's scan speed (~70 s per slide) is the driver's per-opening design, not a regression. A CI-only failure in test_close_anyway_keeps_the_accepted_slides_on_disk was a race in the test (scan auto-accepts, test read the snapshot before accept_all finished; fixed by waiting for owner_decided); agents running a long suite can be killed by their own timeout, so check exit 143. Deferred in #154: worker thread for the ~20 s Close-anyway freeze, scan thread waiting up to an hour after a failed kill, unbounded wait in `_close_first_load`.
