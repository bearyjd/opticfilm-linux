---
title: Watcher service ejected from a cold scanner; holder out after homing round 1
author: Bearyjd
date: 2026-10-31
branch: docs/watcher-cold-eject-2026-10-31
cost: { minutes: 5, tokens: 18043 }
touches:
  - docs/CODEMAPS/architecture.md
  - docs/CONTRIBUTING.md
  - docs/ROADMAP.md
  - scripts/hardware/README.md
  - scripts/hardware/ab_gate_test.sh
  - scripts/hardware/hw_run.sh
  - scripts/hardware/merge_when_green.sh
  - scripts/hardware/tray_and_armc_test.sh
  - tests/test_hardware_scripts.py
  - docs/eject-watch-service.md
---

## Decided
2026-10-31: with the watcher service running (distrobox runner, the fixed unit) on a scanner just switched on with a holder inside (reg 0x01 = 0x00, status 0x4855), one Eject press ran the driver's cold-start sequence (one 1.5 s status-poll timeout logged as "continuing", then homing rounds 1-3, ~5 s each), ended at reg 0x01 = 0x22 and printed "ejected (eject move completed: status word read idle)". About 18 s from press to ejected. The owner saw the holder come out during the first homing round; rounds 2-3 and the final move added nothing visible. Journal: spike/b-eject-watch-2026-10-31/journal-live2.txt (local).
Also verified the same day: the fixed unit starts through distrobox-enter with the scanner off (driver reports "not found on the host USB bus"), and the live stop path works: SIGINT via ExecStop reaches `of135i watch`, which prints "stopped"; 1.75 s, unit inactive, no SIGKILL.

## Tried and ruled out
Not changing the watcher to --cold-homing-rounds 1: it is documented as experimental and unverified, and one observation of the default behaviour is not evidence. Proposed experiment instead: supervised `eject --cold-homing-rounds 1` from a cold scanner with a holder, doctor before and after, then load + one-slide scan to check position; three clean runs before considering it as an opt-in for the unit.

## Watch out
A cold eject timed out twice on 2026-10-26 (homing move 2 of 3), so a single clean run proves little. The watcher holds the scanner: stop the unit (systemctl --user stop opticfilm-eject-watch.service) before running driver commands or tests that open the real device (test_guard_blocks_real_usb fails while it runs). "ejected" is still not proof of release: the owner confirms by looking.
