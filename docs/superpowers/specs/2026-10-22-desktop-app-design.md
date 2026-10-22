# Desktop app: one live window for scanning and reviewing trays — design

Status: draft for the owner's review. Date: 2026-10-22. Builds on the first design (`2026-10-21-plustek-slide-scanner-design.md`), the batch core design (`2026-10-22-batch-core-design.md`) and the code on `main`. This is project 2 of 4 (batch core, **desktop app**, distribution, new enhancements).

## Intent

The owner scans many four-slide trays in a row. They want to press Scan when the scanner says it is ready, keep loading trays, and review the results either as they arrive or all at the end, in one window, without ever waiting for enhancement. The batch core already does the work (batches on disk, background enhancement, flags, previews, owner choices, export). This project is the window on top of it.

## What the owner decided (in conversation)

- **One live window ("layout A"):** a status bar with a Scan button, a queue of trays on the left (each in one stage: ready, scanning, enhancing, reviewing, done), and the selected tray's four slides in the main area with before and after views.
- Enhancement runs in the background while the next tray scans. Review can happen during the batch or at the end; it is the same window.
- The app learns the scanner is ready **from the scanner**; the Scan button only works when it is.
- Slides with no review flag are accepted automatically; only flagged slides ("needs a look") need attention. An owner's choice on a slide is never overwritten.
- Per slide: rotate, brightness and colour adjustments, re-scan a tray, accept all, export.

## Goals

1. A window that follows layout A and drives a `BatchRunner` over a `BatchStore`, usable end to end on the fake scanner (demo mode) with no hardware.
2. Scanner readiness shown live: not connected, busy, not ready, ready, error, unknown; the Scan button enabled only when ready.
3. Tray queue with live stages and progress; selecting a tray shows its four slides with before/after previews, status, and flag reasons in plain words.
4. Review actions: accept or mark "needs a look", rotate left and right, brightness, colour strength, grain and sharpening sliders with "reset to auto", accept all, re-scan a tray (with confirmation).
5. Export dialog (folder, format) that runs off the UI thread with progress, and an explicit "free disk space" action that removes the raw scans of exported trays after showing how much it frees and asking for confirmation.
6. Starting screen: create a new batch (name, album, date, dpi) or open a recent one; clear message if another program already has the batch open.
7. The UI never freezes: scanning, enhancement, previews, export and cleanup all run off the UI thread.
8. Keyboard use for the review loop (next and previous slide, accept toggle, rotate).
9. The Qt-free logic is tested without Qt at the repo's 80 % standard; the Qt layer is smoke-tested headless where PySide6 exists.

## Non-goals

- Flatpak, native installers, udev rule packaging (project 3). The app must be packaging-friendly but does not ship packaging.
- New enhancements: crop and straighten, face-based auto-upright, red-eye removal (project 4). The settings model leaves room for them.
- A full-resolution zoom viewer (previews are at most 1600 px on the long side; the 16-bit files are the exports).
- Real scanner support (still waits on the driver); the real scanner reports status `unknown`, and the app says so.
- Windows, macOS, or a web UI; translations (English only in v1).
- Editing or deleting exported files; a library or album manager.

## Anti-goals

- A window that can lose or silently discard scans, owner choices, or exported files. Anything destructive (re-scan, free disk space) asks first and says what it will do.
- Logic that only works inside Qt (it could not be tested or reused). All behaviour lives in a Qt-free controller; the Qt layer is thin.
- Two writers on one batch (the batch lock already prevents it; the app must handle the refusal gracefully).
- A Scan button that works when the scanner is not known to be ready.
- Looking like a default template; it should look like a purpose-built scanning tool in the KDE style the owner's other app (fastfoto-linux) uses.

## Constraints

- Everything from the earlier designs still holds: Python 3.11+, `uv`, GPL-3.0-or-later, immutable data (frozen dataclasses), small functions and files, tests first, 80 % coverage, no vendor material in the repo, no commands sent to the scanner from this project.
- **Toolkit:** PySide6 (Qt 6) with QML and KDE Kirigami, taken from the distribution, **not** from PyPI (the wheel bundles its own Qt and cannot load the system Kirigami plugin), exactly as the owner's fastfoto-linux app does. So PySide6 and Kirigami are not declared as normal dependencies; `ofscan` and the Qt-free parts must import and test without them.
- **Development environment:** the owner's machine is an immutable Bazzite desktop; the Qt layer is developed and run in a Fedora container (distrobox) with PySide6 and Kirigami installed, and shipped as a Flatpak (project 3) on the KDE runtime with the PySide BaseApp.
- The app holds the batch's writer lock while a batch is open; opening a batch another program holds shows a clear message and offers read-only viewing.
- Memory: previews only; the UI never loads sweeps or 16-bit images. Export and cleanup reuse the core's memory-bounded code.

## Architecture (builder's call unless stated; follows fastfoto-linux)

Three layers, in one package `opticfilm_linux.app`:

1. **Controller (Qt-free).** Owns the `BatchStore`, `BatchRunner` and scanner; polls scanner status; turns runner events into an immutable `AppState` snapshot (scanner status, tray rows with stage and progress, the selected tray with its four slide cards, current dialogs and errors). Exposes commands (`scan`, `rescan`, `select_tray`, `accept`, `edit_slide`, `accept_all`, `export`, `free_space`, `open_batch`, `new_batch`) that never block: long work runs on worker threads and reports back through state changes. Tested without Qt.
2. **Qt bridge.** A thin `QObject` layer: properties, signals and list models (trays, slides) that mirror `AppState`; a timer that drains the runner's event queue and polls scanner status on the UI thread; an image provider serving preview JPEGs with cache invalidation when a slide is updated.
3. **QML pages (Kirigami).** Start page, main page (status bar, tray queue, review pane, adjust panel), export dialog, free-space dialog, confirmation dialogs. No logic beyond layout and calling bridge slots.

Small supporting changes in the core, made in this project: `export_batch` and `cleanup_raw` accept a progress callback (and export stops cleanly when asked), so the UI can show progress and cancel; the core stays Qt-free.

**Demo mode:** `opticfilm --fake SWEEP_DIR` runs the whole app on the fake scanner, so the app is usable and testable with no hardware and is what screenshots and CI use.

## Behaviour details decided by the builder

- Scanner status is polled about twice a second; `unknown` (the real scanner today) shows "Scanner status unknown: scanning is not available yet" and keeps Scan disabled.
- A tray row shows its stage in words and a progress bar while scanning or enhancing (enhancement reports per slide; scanning shows elapsed time, since the scanner gives no progress).
- A slide card shows the "after" preview, a before/after toggle, the status as a badge, and each flag as a plain sentence ("Very dark", "Very bright", "Highlights or shadows are cut off", "Dust or scratches detected").
- Sliders apply after a short pause (about 300 ms after the last movement) so dragging does not queue dozens of re-enhancements; the slide shows a small "updating" marker until its new preview arrives.
- Accept all accepts every non-blank slide in reviewed trays; accepting or editing a slide marks it as the owner's choice (the core guarantees it is never overwritten).
- Re-scan asks for confirmation and says the old scan is kept and the tray's slide settings reset.
- Free disk space lists the exported trays, the space each frees, and what is removed (the raw scans only; previews and exported files stay); the owner confirms.
- Preferences (last batch, window size, default export folder and format) are stored with `QSettings`; nothing else is stored outside the batch.
- Failures (a failed tray, a failed export, a locked batch, a missing folder) appear as a banner in the window with a plain message and the technical detail one click away; nothing is only logged.
- Application id `io.github.bearyjd.OpticFilmLinux`; display name "OpticFilm Linux"; command `opticfilm`.
- **Film type:** v1 is for slides (positive film). The per-tray positive/negative control is not shown, new batches use positive, and negatives remain available through the CLI.
- **Scan button:** enabled only when the scanner reports ready and no scan is pending. The window shows "busy" while the runner has a scan pending or the scanner reports busy. The demo-mode fake scanner reports ready and the app shows busy itself while a scan runs.
- **Export:** formats are 16-bit TIFF (default), 8-bit PNG and JPEG. It writes the accepted slides not yet exported, from trays in review or already exported; flagged and blank slides are never exported. Partial export is normal: exporting again later continues with what is left. "Free disk space" lists only trays whose export is complete.
- **Read-only batch:** opened when another program holds the batch. Every action that writes is disabled (scan, re-scan, edits, accept, accept all, export, free disk space); browsing slides and previews works.
- **Cancel:** cancelling an export keeps the files already written and their exported marks, and the next export continues; cancelling "free disk space" stops before the next tray, because one tray's raw scans are removed all or nothing.
- **Closing the window:** while a scan runs it cannot be interrupted safely, so a dialog says so and offers to wait for it or to keep working. Enhancement is stopped and simply resumes next time; an export is cancelled cleanly first.
- **Preview freshness:** judged from event order and the preview file's modified time; no new event fields are added.
- **New batch:** dpi choices 600, 1200, 2400, 3600 (default) and 7200; the owner picks a parent folder and the batch gets a subfolder named after it. Recent batches are remembered in `QSettings`; a recent path that no longer exists is shown greyed as "not found" and can be removed from the list.
- **Qt tests in CI:** the headless smoke test carries a `qt` marker and runs in a separate, optional Fedora container job; the default CI job is unchanged.

## Left to the builder

Module and file names, QML component breakdown and visual design details (typography, colour, spacing, motion) within the Kirigami style, the exact keyboard shortcuts, state-snapshot field names, thread primitives, the debounce and polling values' initial settings, and how the Qt smoke tests are structured.

## Success looks like

On a machine with PySide6 and Kirigami, `opticfilm --fake SWEEP_DIR` opens, creates a batch, shows "ready", and pressing Scan five times (as it becomes ready) produces five trays that move through scanning and enhancing while the window stays responsive; flagged slides are marked, clean ones are accepted; moving a brightness slider updates that slide's preview without touching its status; Accept all, Export (with progress) and Free disk space work and ask before anything destructive; closing and reopening resumes the batch; starting a second copy shows the "in use" message. The controller's tests pass without Qt at 80 % or more; the headless Qt smoke test loads the main QML and clicks through scan, review and export on the fake scanner.

## Risks

- **Qt and Kirigami are not installable by `uv sync`** (medium): the Qt layer cannot run in the normal CI job. Mitigation: all logic in the Qt-free controller, a separate Fedora CI job for the Qt smoke test, as in fastfoto-linux.
- **UI thread blocking by accident** (medium): a stray call into the core on the UI thread. Mitigation: only the controller touches the core, and only from worker threads; a test that fails if a controller command takes the UI thread's time.
- **The real scanner's readiness may not fit a polled status** (low): the status interface is small and adaptable.
- **Slider changes producing stale previews** (low): the core drops stale results; the bridge also ignores out-of-date preview notifications.

## Calls made without the owner (please check)

- Kirigami (KDE look), mirroring fastfoto-linux, rather than plain Qt Widgets or a GNOME-style toolkit.
- Application id, display name and command (`opticfilm`).
- Demo mode on the fake scanner as the way to run and test without hardware.
- Sliders apply after a short pause rather than live while dragging.
- Free disk space is a separate, explicit dialog (never automatic), listing exported trays only.
- Opening a batch another program holds offers read-only viewing.
- Progress and cancel added to export and cleanup in the core, as part of this project.
- Slides only in v1: the film-type control is hidden.
- Partial export is allowed and "free disk space" only lists fully exported trays.
- Closing during a scan offers to wait for it; enhancement is stopped and resumes later.
- Cancelling an export keeps what was written; cancelling free-space stops between trays.
- The Qt smoke test runs in a separate optional CI job.
