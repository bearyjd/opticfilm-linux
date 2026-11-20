---
title: "On this immutable desktop an export under the home folder fails because /home is a symlink; the reason is in the batch record, not a log"
author: Bearyjd
date: 2026-11-20
branch: main
touches:
  - "src/opticfilm_linux/batch/export.py"
---

## Decided
2026-11-20, first supervised tray: every accepted slide failed to export with "export folder /home is a symlink; choose a regular folder". The export safety check rejects a symlink anywhere above the export folder, and on this Bazzite host /home is a symlink to /var/home. Setting the export folder to the real /var/home/... path worked, and the kept raw sweep meant no rescan. A fix (allow symlinks above the chosen export root, keep rejecting at or below it) was started test first on a local branch; check whether it merged before relying on the workaround.
The app has no error log file. The reason sits on the tray record in the batch file (the auto error field), so read it with jq.

## Tried and ruled out
Reproducing from the dev container: /home is a real directory there, so the check passes. Only the host run shows it. The Qt warnings in the launch terminal are cosmetic.

## Watch out
Auto-export stays held until the empty opening is confirmed; that only gates freeing raw data.

In the code:
- src/opticfilm_linux/batch/export.py: _checked_export_parent, the ancestor symlink walk
- batch file: trays[].auto, auto_error, auto_failed
