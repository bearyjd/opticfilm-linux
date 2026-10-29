<!-- Generated: 2026-10-29 | Source: main @ 65a1a6f | Files: batch/ 13 + pipeline/ 8 | Token estimate: ~700 -->
# Batch + pipeline

## batch/ (on-disk batch, background work)
store.py 221      BatchStore: atomic manifest + tray folder layout; update(change)
manifest.py 95    JSON (de)serialization of BatchManifest
model.py 143      frozen records: SlideSettings, BatchDefaults, tray/slide records
runner.py 310     BatchRunner: scanning + background enhancement; events.py = frozen events
enhance.py 223    enhance_tray(store, number, on_progress): previews, flags, slide records
review.py 184     owner operations; each = one atomic manifest update
flags.py 33       reasons a slide deserves a second look (tuning guesses)
previews.py 36    make_preview(rgb, gamma, long_side=1600) -> 8-bit JPEG source
slots.py 80       per-slot default orientation
export.py 291     export_batch(...): accepted slides -> files; only place raw scans are deleted
cli.py 243        `ofscan batch ...`

## pipeline/ (numpy uint16 RGB(+IR) arrays)
run.py 100        process_sweep(sweep, ProcessOptions, OutputSpec) -> RunResult
split.py 129      split_slides(sweep) -> 4 slides, empty-window detection
dust.py 289       infrared dust/scratch removal
tone.py 118       exposure, fade restoration, negative inversion
detail.py 104     sharpen + grain reduction (dpi-aware)
orient.py 17      quarter-turn + mirror
arrays.py 64      uint16 <-> float32 [0,1], strip-wise processing

## Top-level helpers
output.py 83 sRGB-embedded writer, numbered never-overwritten names | imports.py 84 enhance foreign scans
spikecheck.py 316 / pollreport.py 359  offline verdict + poll-timeout reports for hardware-run folders
