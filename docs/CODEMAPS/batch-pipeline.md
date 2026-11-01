<!-- Generated: 2026-11-01 | Source: main @ 39062e7 | Files: batch/ 13 + pipeline/ 8 + top-level helpers 4 = 25 Python files | Token estimate: ~950 -->
# Batch + pipeline

Paths are relative to `src/opticfilm_linux/`; counts include `__init__.py`.

## Flow

`cli.py:main(argv=None)` -> `batch/cli.py:run_batch(args)` -> `BatchStore` + `BatchRunner`

`BatchRunner.scan_tray()` -> scanner -> `sweepstore.save_sweep(sweep, directory)` -> `enhance_tray(store, number, on_progress=None)` -> previews + flags -> review -> export

`cli.py` scan/process -> `process_sweep(sweep, options, out)` -> split -> skip blanks -> `enhance_slide(slide, options, dpi, *, linear=True)` -> `write_slide(rgb, spec)`

## Batch ownership

- `batch/store.py` (221 lines): locking, atomic manifest, paths and recovery; `update(change)` publishes one manifest change.
- `batch/model.py` (144) + `manifest.py` (95): frozen records and JSON; `SlideKey = tuple[int, int]` means tray number and 1-based holder position.
- `batch/runner.py` (310) + `events.py` (26): scan/rescan/resume, background enhancement and frozen events.
- `batch/enhance.py` (223): sweeps -> split -> previews/flags; version-checked publication. `reenhance_slide(store, number, position)` handles edits.
- `batch/review.py` (184): atomic review updates; `slots.py` (80) composes per-slot orientation.
- `batch/flags.py` (33) + `previews.py` (36): review hints and JPEG previews; `make_preview(rgb, gamma=1.0, long_side=1600)`.
- `batch/cli.py` (243): `ofscan batch ...` commands.

## Export + re-export

`batch/export.py` (335): `export_batch(store, directory, fmt="tiff16", on_progress=None, cancel=None, keys=None, on_selection=None)` -> `ExportResult`. Takes accepted, unexported slides from REVIEW/EXPORTED trays with raw scans, optionally selected by keys. Destination must be outside the batch folder. Reloads raw data for output; checks live settings before marking exported.

`clear_exported(store, keys=None)` atomically clears accepted slides' exported flags and returns chosen trays to REVIEW. A chosen accepted slide in a raw-removed tray rejects the whole update. Re-export writes new numbered files. `cleanup_raw(store, number)` is the raw-deletion boundary; it requires fully exported, current trays.

`app/jobs.py:_nothing_to_write(view, reexport_blocked)` explains empty exports: already exported, not accepted, raw removed, or no accepted slides ready; directs users to re-export when possible.

## Image pipeline

`pipeline/run.py` (99): dust -> optional negative inversion -> tone -> brightness -> grain reduction -> sharpening -> rotation -> mirror.

For linear positives: `tone.subtract_black` -> optional `restore_color` -> `auto_exposure(rgb, gamma_limits=GAMMA_LIMITS, *, linear=True)`. Uses shared linear gain (up to 8x), highlight roll-off and sRGB encoding. Negative/finished-image tone uses bounded gamma, then optional color restoration. `pipeline/tone.py` is 151 lines.

Other stages: `split.py` (129), four-window splitting/blank detection; `dust.py` (289), IR repair; `detail.py` (104), DPI-aware grain/sharpening; `orient.py` (17), rotation/mirror; `arrays.py` (64), uint16/float32 conversion and strip processing.

## Top-level helpers

- `cli.py` (144): devices, scan, process, enhance and batch routing.
- `sweepstore.py` (59): save/load RGB + optional IR `.npy` and `meta.json`; `load_sweep(directory)` memory maps arrays.
- `output.py` (83): `OutputSpec`, TIFF16/PNG8/JPEG with embedded sRGB; exclusive creation reserves sequence numbers and avoids overwrites.
- `imports.py` (84): `process_images(paths, options, out, dpi)` loads foreign scans and calls enhancement with `linear=False`.
