# Hands-free scan to JPEG (design)

Status: draft for the owner's approval. Date: 2026-11-01. Nothing here is built.

## Intent and why

The owner has a very large slide collection. At 7200 dpi one tray of four slides scans in about
14 minutes and leaves about 4 GB of raw data (5.6 GB with the driver's own frame files). A
thousand slides is about a terabyte of raw data, and the owner does not look at slides during
scanning: they load a holder, press Scan, swap, repeat. A same-area comparison on 2026-11-01
(slide 2, same tone curve, same JPEG export at quality 95) showed 7200 dpi is clearly better than
3600 dpi even after the JPEG is shrunk to 3600 size, so the owner wants 7200 raw to JPEG to be
the pipeline, and at the end only the JPEGs left on disk.

## Goals

1. After a tray scan finishes, the app enhances it, accepts every slide, exports JPEGs into the
   album folder, and then frees that tray's raw data, with no click from the owner.
2. The owner can start the next scan right after the previous one ends. Scanning never waits for
   enhancing or exporting of an earlier tray, unless a limit below forces it.
3. Raw data is only freed after the export of that tray is verified (see Safety). A failed export
   keeps the raw data and tells the owner.
4. Slides that look suspicious (blank, dust-flagged, enhancement refused) are exported like the
   rest and listed for later checking. A tray is never held back for one slide.
5. At the end of a session the album folder holds the JPEGs. The batch folder keeps only what is
   needed to find and re-check them (see Decided and Open).
6. JPEG output size is a setting: full size (9600 x 6500 at 7200 dpi) or shrunk to the 3600 dpi
   size (4800 x 3250). Quality stays 95.
7. Every saved JPEG is named from the project and carries as much information about the slide as
   we can record, because after raw data is freed the JPEG is the only record (see Names and
   metadata below).
8. A clear banner or list says what finished, what failed, and what was flagged.

## Downstream: the heirloom contract (names and metadata)

The JPEGs feed the owner's heirloom slide organizer (repo `~/Documents/vibe-code/heirloom`), which
the owner can change as well. Today a file here is named `{album}_{date}_{NNNN}.jpg` and holds only
the sRGB colour profile. Heirloom's own docs (`docs/scanning-new-collection.md`,
`docs/scan-and-organize-convention.md`, `docs/PIPELINE.md`) set what it needs:

- **Scan order = slot order = event order.** File names must sort ascending in physical slot
  order, zero-padded. Heirloom derives event structure from that order, so numbering must follow
  position, never export time.
- **Layout for a slide box (auto-split):** `<collection-root>/box-<N>/<section>/<NNN>.jpg`, where
  `<section>` is one letter `a`-`f` or a zero-padded number `01`-`99` (never three digits), and
  `NNN` is zero-padded and ascending (a section holds up to about 80 slides). The collection root
  lives outside the family library mount.
- **Layout for dated event folders:** `YYYY_MM_event-name/` with files
  `{folder-name}_{original-scan-token}_{NNN}.jpg`. The original scan token is kept as the bridge
  back to an ingested asset.
- **Metadata ownership.** The raw scan's EXIF is heirloom's durable truth (orientation, date,
  GPS), and heirloom bakes the descriptive fields (when, where, who, why) later and only fills
  empty ones. The scan or upload date is never the photo date.

What this scanner app does (my calls, for the owner to check):

- Owner's collections (2026-11-01): boxes, some small (about 10 slides, so one section and a last
  holder that is only partly filled) and some Kodak-style carousels. The layout is therefore the
  slide-box layout `<collection-root>/box-<N>/<section>/<NNN>.jpg`. A carousel is one box;
  because heirloom holds about 80 slides per section, a carousel is split into sections of a set
  size (per-batch setting; the owner's carousels hold 140 slots, so a carousel is one section of up to
  140 = 35 holders, and heirloom's 'about 80 per section' guidance is relaxed on the heirloom
  side; `NNN` is three digits so 140 fits). A "Finish box / next section" action advances the box
  number or section and always starts a fresh holder. The batch records collection slug, box
  number, section and section size. `NNN` counts scanned positions in the section in load order;
  many carousel slots are empty, so the owner loads only occupied slides (compact, consecutive
  numbering, my recommendation) unless the owner says the painted slot numbers matter, in which
  case an optional per-slide 'carousel slot' is stored in the metadata. A slide skipped as blank
  leaves a gap so a rescan keeps its place. Idea, unchecked: skip empty frames inside a holder
  (the driver takes a slide list; non-consecutive lists not verified).
- Output folder layout is chosen per batch: `box-<N>/<section>/` (default, for slide boxes) or a
  dated event folder. The batch records the collection root, box number and section.
- The slide's number comes from its position: section start plus holder and slot, so a rescan
  keeps its place. The internal scan token (tray and slot, for example `T012S3`) is the "original
  scan token" in the event-folder pattern and is stored in the metadata in both layouts.
- Embedded by this app (technical and provenance fields only): scanner make and model (Plustek
  OpticFilm 135i); software name and version and driver revision; scan time as the digitised date;
  scan resolution and the delivered resolution (a shrunk JPEG says 3600 dpi); film mode and
  holder; batch, box, section, tray, slot; the per-slide settings used (brightness, colour,
  grain, sharpening, dust removal) and the tone-curve version; review flags (blank, dust, needs a
  look); calibration integrity with the gain and offset codes; scan duration; and a content hash
  of the slide's raw region so a later rescan can be matched. These go in EXIF plus an XMP block
  in a separate namespace, so they never collide with heirloom's fields.
- Left empty for heirloom: description, photo date, and GPS (unless the owner typed a date into
  the batch, which is written as the photo date and marked as owner-entered).
- Orientation: the app bakes the 180 degree rotation into the pixels and writes EXIF Orientation
  1, so heirloom's `orient-exif` has nothing to correct.
- The sRGB colour profile stays.
- The record kept after a session (manifest and flagged list) uses the same names, so a JPEG and
  its record can always be matched.

Papers, index cards and written descriptions (owner, 2026-11-01): heirloom reads and interprets
these, so this app does no special processing. A card in a slot is scanned and exported in
sequence like any slide, so heirloom keeps its position as the anchor for the slides around it.
A mount image is dropped: this scanner shines light through the slide, so an opaque mount reads
as the sensor's dark level (checked on a real 7200 dpi frame, 2026-11-01) and writing on it is
not captured. Writing on mounts or thick papers needs a flatbed or a camera.

Changes on the heirloom side are listed in the plan, not done here.

## Non-goals

- A new image-quality pipeline. The tone fix (branch fix/tone-black-point) is a separate change
  and is assumed merged first.
- Changing the scanner driver or any motor path.
- Review and editing screens. The UX overhaul is a separate design; this feature must work with
  the current screens and not depend on the new ones.

## Anti-goals (a failure even if it technically works)

- Deleting raw data for a slide whose JPEG is missing, truncated or unreadable.
- Deleting anything outside the batch's own tray folders.
- Starting a scan while a previous scan, load, or eject still holds the scanner.
- Silently skipping a failed tray: the owner must be told.

## Constraints

- Python + PySide6 + QML (Kirigami) app on Linux; the driver runs on the host (bare metal). The
  controller keeps scans, holder moves and exports exclusive today (tests/test_app_controller_
  exclusive.py); goal 2 needs that relaxed carefully or replaced with a queue.
- Memory: the pipeline peaks at about 5.4 GB per slide at 7200 dpi (docs/performance.md); the
  owner's machine has 62 GB. Enhancing one tray while scanning the next must stay inside that.
- Existing pieces to build on: `review.accept_all`, `export.export_batch`, `export.cleanup_raw`,
  `export.free_space`, the `raw_removed` tray flag, `TrayStage`.
- The owner is short on Claude tokens: implementation is done by Codex or grunt tools and
  reviewed separately.

## Decided by the owner

- (2026-11-01, from the plan's three decisions) G1: "load next holder" with auto-eject becomes
  the default way to change holders, built behind a setting and switched on by default only
  after a supervised series of swaps on hardware (it has worked once, and the code marks it
  unproven). G2: confirmed as described below (auto-free after verification, first tray's raw
  kept until checked, tiny record kept). G3: 7200 dpi is the default resolution; JPEG size
  (full or shrunk to 3600 size) stays a per-batch setting, default full size (my call).

- Saved files use the project's naming and carry as much slide information as possible.
- When a session ends, keep a tiny record (manifest and flagged list) and delete everything else.
  Removing the batch folder too happens only at an explicit "finish session" step, with the
  flagged list saved as a text file beside the JPEGs.

- At the end only the JPEGs are left, provided 7200 raw to JPEG is good (the comparison says it is).
- No review during scanning: load and scan only.
- Raw data is kept until the slides are exported, then removed.

## My calls (the owner should check these)

- Automatic mode is a per-batch setting, default on for new batches, with a "keep raw data"
  switch that turns freeing off. A cautious start: the first tray of a batch is exported but its
  raw data is kept until the owner presses one "free raw data" button, so the first run can be
  checked by eye.
- Verification before freeing: every expected JPEG exists, is larger than a minimum size, opens
  with an image decoder, has the expected dimensions for the chosen size, and the manifest marks
  every slide exported. Any failure keeps all raw data of that tray.
- Flagged slides are those the pipeline already marks (blank skipped, `dust`, `needs_look`).
- The default JPEG size is full size; the shrunk size is a setting.
- The first-tray hold applies only to the first tray of a batch. Every later tray frees its raw data automatically once verified.
- If enhancement or export fails for one slide, that slide's raw data is kept and it is listed as failed; the tray's other slides export normally and the tray is not held back.
- JPEG names and the album folder layout stay as today (numbered, never overwritten).
- Shrinking the stored raw data (keeping only the slide area, lossless compression) is a possible
  later saving. It is not part of this feature.

## Open (only the owner can answer)

- Which heirloom layout these slides use: the `box-<N>/<section>/<NNN>.jpg` slide-box layout
  (my guess) or dated event folders. And whether skipped blank slots leave gaps in the numbering
  or are closed up (my call: gaps, so a slot number always means the same physical slot).

## Left to the builder

- The queue design and exact states (extending `TrayStage` or a separate worker).
- Where the verification lives, the banner wording, and test layout.
- How to relax the exclusivity rules safely, with tests for every refusal that must stay.
