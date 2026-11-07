<!-- Generated: 2026-11-06 | Files scanned: 23 batch + 8 pipeline + 11 top-level = 42 | Token estimate: ~980 -->
# Batch + pipeline (+ manifest data model)

Paths relative to `src/opticfilm_linux/`.

## Flow
cli.py:main -> batch/cli.py:run_batch(args)  (`ofscan batch ...`) -> BatchStore + BatchRunner
BatchRunner.scan_tray(skipped=()) -> scanner -> sweepstore.save_sweep -> enhance_tray(store, n, on_progress)
 -> previews + flags -> review -> export_batch -> verify_tray_export -> cleanup_raw
ofscan scan/process: process_sweep(sweep, options, out) -> split -> skip blanks -> enhance_slide -> write_slide
imports.py process_images(paths, options, out, dpi): foreign scans, linear=False.

## Data model (JSON manifest, MANIFEST_VERSION = 1; no database)
model.py 311 frozen records: BatchManifest(defaults, trays) > TrayRecord > SlideRecord; SlideKey = (tray, 1-based position)
 TrayStage: scanning|scanned|enhancing|review|exported|failed; SlideStatus: pending|needs_look|accepted|blank|skipped
 Flag: too_dark|too_bright|clipping|dust; per-slide SlideSettings, rotation/mirror; ExportRecord receipts
manifest.py 297 JSON (de)serialization, ignores unknown receipt keys; store.py 263 BatchStore: file lock, atomic write,
 open(dir, readonly)/create, snapshot(), update(change), update_tray_if, recover_interrupted(), path helpers
metadata.py 178 EstimatedDate (year+month), MetaOverride, resolve(): description/date at batch > tray > slide levels
layout.py/slots.py (positions, orientation), empty.py + skipped.py (blank vs deliberately skipped openings),
reorient.py + preview_transform.py + turn_masters.py (quick turns without re-enhance), flags.py, previews.py

## Runner + review
runner.py 667 BatchRunner: scan_tray/rescan_tray/resume, edit_slide/edit_many/restore_many, set_batch_orientation, stop/close;
 events.py: frozen events incl. ScanFinished(number, ok, driver_exit)
enhance.py 306 enhance_tray, reenhance_slide (version-checked publish); review.py 681 atomic edits: accept_tray(automatic),
 confirm_empty_frames, *_many bulk edits, edit_meta_many/edit_tray_meta/edit_batch_meta, undo via restore_*

## Export, verify, cleanup
export.py 797 export_batch(store, directory, fmt="tiff16", on_progress, cancel, keys, on_selection) -> ExportResult;
 destination outside batch folder; _write_export records receipt (digest + metadata signature)
 clear_exported(store, keys) for re-export; verify_tray_export; cleanup_raw(store, n, release_first=False);
 freeable_trays, free_space. export_contract.py: pure specs/receipt validation/cleanup eligibility.
 cleanup_recovery.py: recover_interrupted_cleanup(store, n) after a crashed cleanup (never deletes scans).

## Metadata strip + JPEG rewrite
meta_sync.py 151: current_signature, metadata_out_of_date, out_of_date_keys(manifest),
 update_exported_metadata(store, keys=None, on_progress, cancel) -> MetaUpdateResult; renews receipts.
jpeg_rewrite.py 178: replace_xmp(data, update), updated_packet(old, estimate), rewrite_xmp_file(...): replaces only the
 ofscan XMP segment, atomically, no re-encode; idempotent after a crash; RewriteRefused on read-only files.

## Image pipeline
pipeline/run.py: dust -> invert (negatives) -> tone -> brightness -> grain -> sharpen -> rotate -> mirror.
tone.py: subtract_black -> restore_color -> auto_exposure(rgb, gamma_limits, *, linear=False) (shared gain <=8x, roll-off, sRGB).
split.py four-window split + blank detection; dust.py IR repair; detail.py DPI-aware; orient.py; arrays.py uint16/float32.

## Top-level helpers
output.py 330 OutputSpec, TIFF16/PNG8/JPEG with sRGB, exclusive numbered creation; sweepstore.py save/load .npy + meta.json;
provenance.py immutable scan evidence; model.py ProcessOptions/ScanSettings; pollreport.py, spikecheck.py (diagnostics); worker.py.
