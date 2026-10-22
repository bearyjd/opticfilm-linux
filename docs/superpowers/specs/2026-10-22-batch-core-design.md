# Batch core: trays, per-slide settings, background enhancement — design

Status: draft for the owner's review. Date: 2026-10-22. Builds on `docs/superpowers/specs/2026-10-21-plustek-slide-scanner-design.md` and the code merged to `main` (package `opticfilm_linux`, CLI `ofscan`).

## Where this fits

The owner wants a desktop app for scanning many four-slide trays in a row, plus more enhancement features and packaging. That is four projects, agreed in this order, each shippable on its own:

1. **Batch core (this document).** No window. The data model, storage, scanner status interface and background worker everything else sits on.
2. Desktop app (PySide6, "layout A" below).
3. Distribution (Flatpak, native install, USB access rule, CI).
4. New enhancements: crop and straighten, face-based auto-upright, red-eye removal. Last, because they are tuned on real slides, which the owner does not have yet.

Not buildable yet, and out of every project above: multi-exposure merging (the driver cannot scan several exposures per pass), a scanner-specific colour profile (needs a calibration target; Plustek's profile cannot be shipped), and the real scanner "ready" signal (needs driver support).

## Intent

The owner loads a tray; the scanner orients it mechanically and signals that it is ready; they press Scan and four slides are scanned. They repeat for many trays. Scanning one tray takes about 6 minutes at 3600 dpi, enhancing it about 15 seconds, so enhancement of finished trays should run in the background while the next tray scans. They can review as trays finish or all at the end. This project builds the engine for that, so the app (project 2) is thin.

## The app this serves (for context; built in project 2)

One live window. A status bar shows the scanner state and a Scan button that only works when the scanner is ready. A queue of trays runs down the left, each in one stage: ready, scanning, enhancing, reviewing, done. The selected tray's four slides fill the main area with before and after views and a "needs a look" flag on doubtful slides. Per slide the owner can rotate and adjust brightness and colour; per tray they can re-scan; they can accept all and export. Review can happen during the batch or at the end; it is the same window.

## Goals

1. A **batch** (a named folder on disk) holds any number of **trays**; each tray holds the raw sweep and up to four **slide records**.
2. **Non-destructive**: the raw sweep is kept; everything shown or exported is derived from raw plus settings, so changing a setting never needs a re-scan.
3. **Resumable**: closing the program or a crash at any point loses at most the tray being scanned; on reopening, the batch is exactly as left, and interrupted enhancement continues.
4. **Background enhancement** of each tray while the next scans, with no dependency on any UI thread, reporting progress through an event stream.
5. A **scanner status interface** (not connected, busy, no tray / not ready, ready, error) the app can poll, with the fake scanner able to play a scripted sequence of statuses and scans.
6. **Per-slide settings** (rotation in 90° steps, brightness offset, colour-restoration strength, plus overrides of grain and sharpening strength) and the strengths for colour, grain and sharpening exposed everywhere the pipeline is used, including the CLI.
7. **Previews** (before and after, 8-bit JPEG, long side 1600 px) generated after enhancement, so a UI never has to read the huge raw files.
8. **"Needs a look" flags** with reasons, computed after enhancement.
9. **Export** of accepted slides to an output folder using the existing writer (numbered, never overwrites).
10. A small `ofscan batch` command group so the core can be used and tested headlessly.

## Non-goals

- Any window, widget or Qt import. The core stays toolkit-agnostic.
- Flatpak or any packaging.
- Crop and straighten, face-based auto-upright, red-eye removal, multi-exposure merging, device colour profiles.
- Real scanning. The real scanner class keeps raising `NotSupportedYet`; its status is reported as unknown until the driver exists.
- Network features, accounts, cloud.

## Anti-goals

- Never lose or silently delete raw scans. Raw sweeps are deleted only by an explicit "clean up raw scans" action after export, never automatically.
- Never leave a half-written sweep that looks complete. A scan writes into a temporary folder and is renamed into place only when finished.
- Never block a caller. Scanning and enhancing run on worker threads; callers read state and events.
- Never two writers to the batch manifest. All changes go through one lock and are written atomically.

## Constraints

- Everything in the first design's Global Constraints still holds: Python 3.11+, `uv`, GPL-3.0-or-later, immutable data flow (frozen dataclasses, new objects instead of mutation), functions under 50 lines, files under 800, tests first, 80 % coverage, hardware tests marked and excluded, no vendor material in the repo, no commands sent to the scanner.
- Memory: a 3600 dpi sweep is about 1.3 GB (RGB plus infrared). Sweeps stay memory-mapped; only one tray is enhanced at a time; peak memory stays near the sweep size (the existing strip-wise processing already ensures this).
- Disk: raw data is about 1.3 GB per tray at 3600 dpi (20 trays about 26 GB). The batch folder's location is the owner's choice; nothing is written outside it except exports.
- The core must work with the existing `FakeScanner` and synthetic sweeps, with no hardware.

## Decisions

**The owner decided:** enhancement runs in the background during scanning (they deferred to the builder and agreed to layout A); review can happen during or after scanning; the order of the four projects; the app learns the scanner is ready from the scanner, never by guessing; slides with no review flag are accepted automatically and only flagged slides need a look.

**Decided by the builder (the owner can overrule):**
- Batches are persistent and named; reopening a batch resumes it.
- A batch folder contains `batch.json` (manifest, written atomically) and `trays/NNNN/` with `sweep/` (the existing sweep format), `previews/` (`SS-before.jpg`, `SS-after.jpg`) and nothing else. Exports go to a separate output folder chosen at export time.
- **The owner starts every scan.** The runner has `scan_tray()`, called by the caller (later, the Scan button); it raises `ScannerNotReady` unless the scanner's status is ready, and it never loops by itself. `scan_tray()` blocks its own worker thread for the length of the scan; `status()` must be callable from another thread meanwhile.
- Tray stages, in order: `SCANNING`, `SCANNED` (raw saved, waiting for enhancement), `ENHANCING`, `REVIEW`, `EXPORTED`, plus `FAILED`. Slide statuses: `PENDING`, `NEEDS_LOOK`, `ACCEPTED`, `BLANK`. There is no per-slide re-scan: a sweep covers the whole holder, so re-scanning is per tray only.
- **Re-scan** keeps the old sweep (`sweep-1`, `sweep-2`, ... in the tray folder; the latest is the live one) and resets the tray's slide records, settings and flags, because it is a new image. The tray returns to `SCANNING`, then follows the normal stages. Old sweeps are removed only by the explicit raw-cleanup action.
- **Where settings live:** dpi and infrared are chosen per batch and fixed at scan time; positive or negative mode is a per-tray setting and can be changed after scanning (it re-enhances the tray). Grain, sharpening, colour strength and brightness defaults are per batch, overridable per slide.
- **Setting ranges:** colour-restoration strength 0.0 to 1.0 (0 = off), replacing the existing `restore_color` boolean; grain strength and sharpening amount keep their current 0-and-up meaning (0 = off); brightness offset in photographic stops, -2.0 to +2.0, applied as a gain of 2^offset on the exposure-corrected image.
- **Acceptance (the owner decided: automatic).** When a tray finishes enhancing, slides with no flag become `ACCEPTED` automatically and flagged slides become `NEEDS_LOOK`; the owner can change any slide's status. Once the owner has accepted, rejected or edited a slide, their choice stands: re-enhancement refreshes its previews and flags but never changes a status the owner set. Export is always an explicit action; nothing is exported just because it was auto-accepted.
- **Re-enhancement unit:** one slide. A change to a slide's settings queues one job on the same enhancement queue; its previews and flags are regenerated; export always recomputes from raw plus settings and never uses previews.
- **Blank windows** keep a slide record with status `BLANK` (so positions stay 1 to 4) and produce no files or previews; `skip_blank` becomes a batch default.
- **Export** writes every `ACCEPTED` slide not yet exported (each slide record remembers it was exported); a tray becomes `EXPORTED` when all its non-blank slides are accepted and written, and otherwise stays in `REVIEW`.
- Recovery on reopen: a tray found in `SCANNING` becomes `FAILED` (needs re-scan; its temporary folder is removed); a tray found in `ENHANCING` is queued again.
- One scan at a time (the hardware allows nothing else); one enhancement at a time, on a single worker thread, behind a job queue; both run beside the caller's thread. Progress goes out as frozen event dataclasses on a thread-safe queue.
- Scanner status is polled (`status()`), not pushed; the polling interval is the app's business.
- Review-flag rules and thresholds are named constants, tuned later on real slides: very dark or very bright result, high clipped fraction, large defect area, blank window. (A "boundaries moved" flag needs real scans and is left out of this project.)
- Rotation is in 90° steps only here; arbitrary-angle straightening belongs to project 4.
- Manual per-slide settings apply on top of the automatic pipeline, never replace it; "reset to auto" removes the overrides.

## Left to the builder

Module layout, manifest field names and version number, the exact event types, thread primitives, preview encoding quality, flag thresholds' initial values, CLI flag spellings, and how the fake scanner's script is expressed, provided the goals and constraints above hold.

## Success looks like

With the fake scanner scripted to report not ready, then ready, and a test caller pressing scan five times (calling `scan_tray()` each time the status is ready), a runner on an empty batch ends with five trays in `REVIEW`, previews on disk, flags where the synthetic data warrants them; killing the runner mid-scan and mid-enhancement and reopening resumes correctly; changing a slide's brightness and re-enhancing needs no scanner; export writes only accepted slides and never overwrites; `ofscan batch` can drive all of this from a terminal; the whole suite passes with at least 80 % coverage.

## Risks

- **Threading bugs** (medium): races between the scan thread, the enhancement worker and a UI reading state. Mitigation: one manifest lock, immutable snapshots handed to readers, tests that script interleavings.
- **Disk usage** (medium): 26 GB per 20 trays. Mitigation: show usage in the app (project 2), explicit raw cleanup.
- **Flag thresholds are guesses** (low): tuned on real slides later.
- **The driver's real status signal may not fit the polled model** (low): the interface is small enough to adapt.

## Calls made without the owner (please check)

- Persistent, named, resumable batches on disk (instead of an in-memory session).
- Raw scans kept until an explicit cleanup action (never automatic).
- Rotation limited to 90° steps in this project.
- A headless `ofscan batch` command group is included.
- No automatic scan loop: every scan is started by the caller.
- An owner's explicit choice on a slide is never overwritten by re-enhancement.
- Re-scan is per tray and keeps the old sweep; slide settings reset on re-scan.
- Mode is per tray; dpi and infrared per batch.
- Brightness offset is in stops (-2 to +2); colour strength 0 to 1 replaces the boolean.
- The "boundaries moved" review flag is dropped from this project.
- Polling for scanner status rather than push events.
