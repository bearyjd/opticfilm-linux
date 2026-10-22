# Batch Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A toolkit-agnostic engine for scanning many four-slide trays: persistent resumable batches, non-destructive per-slide settings, background enhancement while the next tray scans, review flags and previews, export, and a headless `ofscan batch` CLI.

**Architecture:** A `BatchStore` owns an atomically written JSON manifest and the on-disk layout. A `BatchRunner` drives a `Scanner` (new `status()` method) with one scan worker and one enhancement worker, reporting through an event queue. Enhancement reuses the existing pipeline; settings are stored per slide and applied on top of batch defaults; export always recomputes from raw sweeps.

**Tech Stack:** Python 3.11+ stdlib (`threading`, `concurrent.futures`, `queue`, `json`, `os.replace`), numpy, Pillow, OpenCV (already dependencies). No Qt.

**Spec:** `docs/superpowers/specs/2026-10-22-batch-core-design.md` (approved 2026-10-22). Existing code: package `opticfilm_linux` on `main` (pipeline, scanner, output, sweepstore, cli).

## Global Constraints

- Python 3.11+, managed with `uv`; GPL-3.0-or-later; no Qt or any GUI import anywhere in the package.
- Immutable data flow: frozen dataclasses, new objects instead of mutation; pipeline steps return new arrays.
- Tests first (RED, then GREEN); coverage gate 80 % (`uv run pytest --cov=opticfilm_linux --cov-fail-under=80`); `uv run ruff check .` and `ruff format --check .` clean.
- Functions under 50 lines, files under 800 lines; no `print()` outside `cli.py`.
- No vendor material in the repo; nothing in this plan sends commands to the scanner; hardware tests keep `@pytest.mark.hardware` and stay out of the default run.
- Memory: sweeps stay memory-mapped; one tray enhanced at a time; peak memory near the sweep size.
- Anti-goals: never lose or silently delete raw scans (raw is removed only by the explicit `cleanup_raw`); a scan writes into a temporary folder renamed into place only when complete; no caller is ever blocked by scanning or enhancing; exactly one writer path to the manifest, under one lock, written atomically.
- Setting ranges: rotation in {0, 90, 180, 270}; brightness offset in stops -2.0 to +2.0; colour-restoration strength 0.0 to 1.0 (0 = off); grain strength and sharpening amount >= 0 (0 = off).

## Review Focus

Failure modes the spec implies, most likely first; each has a test in the owning task:

1. **Interrupted scan or crash** (power cut, Ctrl-C, killed process): no half-written sweep may look complete; on reopen the tray is `FAILED` and its temporary folder is gone. Tasks 4, 7.
2. **Scanner not ready when Scan is requested**: `ScannerNotReady`, and no tray record is created. Task 7.
3. **Owner's explicit choice overwritten by re-enhancement**: it must never happen, including after a tray-mode change. Tasks 6, 8.
4. **Concurrent manifest writers** (scan worker, enhancement worker, edits from a caller): no lost updates, never a corrupt or partial `batch.json`. Tasks 4, 8.
5. **Exporting twice, or with unreviewed slides**: no overwrites, no duplicates, flagged slides never exported without the owner accepting them; `cleanup_raw` only inside the batch folder and only for an exported tray, even through a symlink. Task 9.
6. **Corrupt manifest or sweep** on open: a clear error and exit code 2, not a traceback. Tasks 3, 4, 10.

---

### Task 1: Pipeline options (brightness, rotation, colour strength) and CLI strengths

**Files:**
- Create: `src/opticfilm_linux/pipeline/orient.py`, `tests/test_orient.py`
- Modify: `src/opticfilm_linux/pipeline/tone.py`, `src/opticfilm_linux/pipeline/run.py`, `src/opticfilm_linux/cli.py`, `tests/test_tone.py`, `tests/test_run.py`, `tests/test_cli_scan.py`

**Interfaces:**
- Produces: `orient.rotate(rgb: np.ndarray, degrees: int) -> np.ndarray` (clockwise; `degrees` in {0, 90, 180, 270}, else `ValueError`; always a new C-contiguous uint16 array); `tone.adjust_brightness(rgb: np.ndarray, ev: float) -> np.ndarray` (gain `2**ev`, clipped, strip-wise; `ev == 0` returns an equal copy); `run.ProcessOptions` gains `brightness_ev: float = 0.0` and `rotation: int = 0`, and `restore_color: bool` is replaced by `color_strength: float = 1.0`; `enhance_slide(slide, options, dpi)` order becomes dust removal, negative inversion, auto exposure, brightness, colour restoration (skipped when strength is 0), grain, sharpening, rotation. CLI options `--sharpen FLOAT` (0.5), `--grain FLOAT` (0.3), `--color-strength FLOAT` (1.0) on `scan` and `process`.

- [ ] **Step 1: Write failing tests.** `test_orient.py`: `test_rotate_90_is_clockwise` (3x2 array: the top-left pixel ends up top-right of the 2x3 result), `test_rotate_180_twice_is_identity`, `test_rotate_zero_returns_equal_new_array` (`not np.shares_memory`), `test_rotate_rejects_45` (`ValueError`), `test_rotate_keeps_uint16`. `test_tone.py`: `test_brightness_plus_one_stop_doubles_midgrey` (0.25 of full scale becomes 0.5, within 1 level), `test_brightness_minus_one_stop_halves`, `test_brightness_zero_is_identity`, `test_brightness_clips_at_white`, `test_brightness_all_black_has_no_nan`. `test_run.py`: `test_rotation_90_swaps_output_shape`, `test_color_strength_zero_skips_restoration` (output of `ProcessOptions(color_strength=0.0)` differs from the default on a faded synthetic slide), `test_brightness_option_brightens` (mean of output with `brightness_ev=1.0` exceeds the default's). `test_cli_scan.py`: `test_strength_flags_reach_process_options` (spy on `cli.process_sweep` like the existing `--no-infrared` test; `--sharpen 0 --grain 0 --color-strength 0` give `sharpen_amount == 0`, `grain_strength == 0`, `color_strength == 0`).
- [ ] **Step 2: Run** `uv run pytest tests/test_orient.py tests/test_tone.py tests/test_run.py tests/test_cli_scan.py -q`. Expected: FAIL (new names missing).
- [ ] **Step 3: Implement** the signatures above. `adjust_brightness` uses `map_strips`; `rotate` uses `np.rot90(rgb, k=-degrees // 90)` then `np.ascontiguousarray`. Apply the CLI floats through `ProcessOptions`.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`. Expected: PASS.
- [ ] **Step 5: Commit:** `feat: brightness, rotation and strength options in the pipeline and CLI`.

### Task 2: Scanner status interface

**Files:**
- Modify: `src/opticfilm_linux/scanner/base.py`, `src/opticfilm_linux/scanner/fake.py`, `src/opticfilm_linux/scanner/community.py`, `tests/test_fake_scanner.py`, `tests/test_usb.py`

**Interfaces:**
- Produces: `class ScannerStatus(StrEnum)`: `NOT_CONNECTED`, `BUSY`, `NOT_READY`, `READY`, `ERROR`, `UNKNOWN`; `class ScannerNotReady(ScanError)`; `Scanner.status(self) -> ScannerStatus` added to the protocol; `FakeScanner.__init__(self, sweep_dir: Path, statuses: Sequence[ScannerStatus] = (ScannerStatus.READY,), before_scan: Callable[[], None] | None = None)`: `status()` returns the scripted statuses in order and then repeats the last, thread-safely; `scan_holder` calls `before_scan()` first when given; `CommunityDriverScanner.status()` returns `UNKNOWN`.

- [ ] **Step 1: Write failing tests:** `test_default_fake_status_is_ready`; `test_scripted_statuses_play_in_order_then_repeat_the_last` (`(NOT_READY, READY)` gives NOT_READY, READY, READY); `test_before_scan_runs_once_per_scan`; `test_community_scanner_status_is_unknown`; `test_scanner_not_ready_is_a_scan_error`; the existing protocol test must still pass with `status` required.
- [ ] **Step 2: Run** `uv run pytest tests/test_fake_scanner.py tests/test_usb.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the interface; guard the status iterator with a `threading.Lock`.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit:** `feat: scanner status interface with a scripted fake`.

### Task 3: Batch model and manifest serialization

**Files:**
- Create: `src/opticfilm_linux/batch/__init__.py`, `src/opticfilm_linux/batch/model.py`, `src/opticfilm_linux/batch/manifest.py`, `tests/test_batch_model.py`, `tests/test_manifest.py`

**Interfaces:**
- Produces (`model.py`): `TrayStage(StrEnum)`: `SCANNING, SCANNED, ENHANCING, REVIEW, EXPORTED, FAILED`; `SlideStatus(StrEnum)`: `PENDING, NEEDS_LOOK, ACCEPTED, BLANK`; `Flag(StrEnum)`: `TOO_DARK, TOO_BRIGHT, CLIPPING, DUST`; frozen `SlideSettings(rotation: int = 0, brightness_ev: float = 0.0, color_strength: float | None = None, grain_strength: float | None = None, sharpen_amount: float | None = None)` (`None` = batch default; ranges from Global Constraints enforced in `__post_init__` with `ValueError`); frozen `SlideRecord(position: int, status: SlideStatus = PENDING, flags: tuple[Flag, ...] = (), settings: SlideSettings = SlideSettings(), owner_decided: bool = False, exported: bool = False)`; frozen `TrayRecord(number: int, stage: TrayStage, mode: Literal["positive", "negative"] = "positive", sweep_version: int = 1, slides: tuple[SlideRecord, ...] = (), error: str | None = None, raw_removed: bool = False)`; frozen `BatchDefaults(dpi: int = 3600, infrared: bool = True, mode: Literal["positive", "negative"] = "positive", skip_blank: bool = True, remove_dust: bool = True, color_strength: float = 1.0, grain_strength: float = 0.3, sharpen_amount: float = 0.5)`; frozen `BatchManifest(name: str, album: str = "", date: str | None = None, defaults: BatchDefaults = BatchDefaults(), trays: tuple[TrayRecord, ...] = (), version: int = 1)`.
- Produces (`manifest.py`): `MANIFEST_VERSION = 1`; `class ManifestFormatError(ValueError)`; `to_json(manifest: BatchManifest) -> str`; `from_json(text: str) -> BatchManifest`.

- [ ] **Step 1: Write failing tests:** `test_settings_reject_out_of_range` (parametrized: rotation 45, brightness 3.0, brightness -2.5, color_strength 1.5, grain -1, sharpen -0.1: `ValueError`); `test_settings_accept_the_range_edges`; `test_records_are_frozen`; `test_manifest_roundtrip_with_trays_and_slides` (a manifest with two trays, flags, settings with `None` and values, `owner_decided`, `raw_removed`; `from_json(to_json(m)) == m`); `test_json_uses_enum_values_not_names` (`'"review"'` appears, no `TrayStage`); `test_unknown_manifest_version_raises` (`ManifestFormatError`, message names the version); `test_garbage_json_raises_manifestformaterror`; `test_missing_key_names_the_key` (`ManifestFormatError` with `"defaults"` in the message); `test_wrong_type_raises_manifestformaterror` (rotation as a string).
- [ ] **Step 2: Run** `uv run pytest tests/test_batch_model.py tests/test_manifest.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** both modules; `from_json` converts every `KeyError`, `TypeError`, `ValueError` from malformed content into `ManifestFormatError` with the offending key.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit:** `feat: batch data model and manifest serialization`.

### Task 4: BatchStore, atomic writes and crash recovery

**Files:**
- Create: `src/opticfilm_linux/batch/store.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: Task 3 types and `manifest.to_json/from_json`.
- Produces: `class BatchError(ValueError)`; `class BatchStore`: `create(cls, directory: Path, name: str, album: str = "", date: str | None = None, defaults: BatchDefaults = BatchDefaults()) -> BatchStore` (raises `BatchError` if `batch.json` exists), `open(cls, directory: Path) -> BatchStore` (raises `BatchError` if missing, `ManifestFormatError` if corrupt), `directory: Path`, `snapshot(self) -> BatchManifest`, `update(self, change: Callable[[BatchManifest], BatchManifest]) -> BatchManifest` (holds one lock while applying `change` and writing `batch.json.tmp` then `os.replace`; on any failure the on-disk and in-memory manifests are unchanged), `tray_dir(self, number: int) -> Path` (`trays/0001`), `sweep_dir(self, number: int, version: int) -> Path` (`trays/0001/sweep-1`), `sweep_tmp_dir(self, number: int, version: int) -> Path` (`trays/0001/sweep-1.tmp`), `preview_path(self, number: int, position: int, kind: Literal["before", "after"]) -> Path` (`trays/0001/previews/02-after.jpg`), `recover_interrupted(self) -> tuple[int, ...]` (a `SCANNING` tray becomes `FAILED` with `error="scan was interrupted"` and its `*.tmp` sweep folder is removed; an `ENHANCING` tray becomes `SCANNED`; returns the numbers of all `SCANNED` trays in ascending order).

- [ ] **Step 1: Write failing tests:** `test_create_then_open_roundtrip`; `test_create_twice_raises_batcherror`; `test_open_missing_raises_batcherror`; `test_open_corrupt_manifest_raises_manifestformaterror`; `test_update_writes_atomically_when_replace_fails` (monkeypatch `os.replace` to raise `OSError`: the file on disk still parses to the old manifest, `snapshot()` is unchanged, and no `.tmp` file is left); `test_update_is_not_applied_when_change_raises`; `test_concurrent_updates_lose_nothing` (8 threads x 50 updates each appending a tray: 400 trays, file parses); `test_path_helpers_format` (the four path shapes above); `test_recover_interrupted_scan_marks_failed_and_removes_tmp` (a `SCANNING` tray with a `sweep-1.tmp` folder holding a file: afterwards `FAILED`, folder gone, a finished `sweep-1` folder of another tray untouched); `test_recover_enhancing_becomes_scanned_and_is_returned`; `test_recover_returns_scanned_trays_in_order`.
- [ ] **Step 2: Run** `uv run pytest tests/test_store.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** `update` copies nothing mutable: `change` receives the frozen manifest and returns a new one. Remove the temporary folder with `shutil.rmtree` only for paths that equal `sweep_tmp_dir(...)`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit:** `feat: batch store with atomic manifest writes and crash recovery`.

### Task 5: Previews and review flags

**Files:**
- Create: `src/opticfilm_linux/batch/previews.py`, `src/opticfilm_linux/batch/flags.py`, `tests/test_previews.py`, `tests/test_flags.py`

**Interfaces:**
- Consumes: `Flag` (Task 3), `defect_mask` (`pipeline/dust.py`).
- Produces: `make_preview(rgb: np.ndarray, gamma: float = 1.0, long_side: int = 1600) -> np.ndarray` (uint8 (h, w, 3); aspect preserved, long side at most `long_side`, never upscaled; display gamma applied as `x ** (1 / gamma)`); `write_preview(rgb8: np.ndarray, path: Path) -> None` (JPEG quality 90, creates parent folders); `compute_flags(enhanced: np.ndarray, ir: np.ndarray | None, dpi: int) -> tuple[Flag, ...]` in the fixed order `TOO_DARK, TOO_BRIGHT, CLIPPING, DUST`, using named constants `DARK_MEAN = 0.12`, `BRIGHT_MEAN = 0.88`, `CLIP_FRACTION = 0.02`, `DUST_FRACTION = 0.005` (fractions of full scale / of the image).

- [ ] **Step 1: Write failing tests:** previews: `test_long_side_is_capped_and_aspect_kept` (3000x2000 gives 1600x1067 within 1 px), `test_small_image_is_not_upscaled`, `test_gamma_brightens`, `test_write_preview_creates_a_readable_jpeg`. Flags: `test_dark_image_is_flagged_too_dark`, `test_bright_image_is_flagged_too_bright`, `test_heavy_clipping_is_flagged`, `test_large_defect_area_in_ir_is_flagged_dust` (IR with a dark patch over more than 0.5 % of the area), `test_no_ir_never_flags_dust`, `test_clean_midtone_image_has_no_flags`, `test_flags_come_in_fixed_order` (a dark, clipped image: `(TOO_DARK, CLIPPING)`).
- [ ] **Step 2: Run** `uv run pytest tests/test_previews.py tests/test_flags.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** with `cv2.resize(..., interpolation=cv2.INTER_AREA)` and Pillow for the JPEG.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit:** `feat: batch previews and review flags`.

### Task 6: Tray and slide enhancement

**Files:**
- Create: `src/opticfilm_linux/batch/enhance.py`, `tests/test_enhance_tray.py`

**Interfaces:**
- Consumes: `BatchStore` (Task 4), model (Task 3), `ProcessOptions`, `enhance_slide` (Task 1), `split_slides`, `is_blank`, previews and flags (Task 5), `load_sweep`.
- Produces: `options_for(defaults: BatchDefaults, mode: Literal["positive", "negative"], settings: SlideSettings) -> ProcessOptions` (slide overrides win over batch defaults when not `None`; `brightness_ev` and `rotation` come from the slide; `skip_blank` from defaults); `enhance_tray(store: BatchStore, number: int, on_progress: Callable[[int, int], None] | None = None) -> None`; `reenhance_slide(store: BatchStore, number: int, position: int) -> None`; `class RawRemoved(BatchError)`.

`enhance_tray` behaviour (decided): sets the tray `ENHANCING`; loads the live sweep (`sweep_dir(number, tray.sweep_version)`); splits it; per slide: a blank window becomes a `BLANK` record with no previews; otherwise it writes the "before" preview (raw rgb, `gamma=2.2`) and the "after" preview (enhanced), computes flags, and sets the status to `ACCEPTED` (no flags) or `NEEDS_LOOK` (flags) unless the record has `owner_decided`, in which case status and settings are kept; finally the tray becomes `REVIEW`. `SlideSplitError` or `SweepFormatError` makes the tray `FAILED` with the message in `error`. `reenhance_slide` regenerates one slide's previews and flags from its stored settings and never changes the status of an `owner_decided` record; raises `RawRemoved` if `raw_removed`.

- [ ] **Step 1: Write failing tests** (helper: a `BatchStore` in `tmp_path` with a tray record in stage `SCANNED` and `make_sweep(dpi=600)` saved at its `sweep_dir`): `test_enhance_tray_reaches_review_with_four_slides_and_previews`; `test_unflagged_slides_are_auto_accepted` (default synthetic slides: all `ACCEPTED`); `test_dark_slide_is_flagged_and_needs_a_look` (one aperture replaced by uniform 3 % brightness: that record is `NEEDS_LOOK` with `TOO_DARK`); `test_blank_window_becomes_blank_record_without_previews` (`filled=(True, False, True, True)`: position 2 is `BLANK`, no preview files for it); `test_owner_decision_survives_re_enhancement` (set `owner_decided=True`, status `NEEDS_LOOK` on a clean slide, run `enhance_tray` again: status still `NEEDS_LOOK`); `test_reenhance_slide_applies_rotation_to_the_after_preview` (settings `rotation=90`: the after preview's width and height swap compared with before); `test_reenhance_slide_regenerates_the_preview_file` (file bytes change after a brightness change); `test_corrupt_sweep_fails_the_tray_with_a_message` (truncate `rgb.npy`: stage `FAILED`, `error` non-empty); `test_unsplittable_sweep_fails_the_tray` (all-zero sweep); `test_options_for_prefers_slide_overrides` (grain override 0.0 beats default 0.3; `None` falls back); `test_progress_callback_reports_each_slide` (calls `(1, 4)` ... `(4, 4)`); `test_reenhance_after_raw_removed_raises`.
- [ ] **Step 2: Run** `uv run pytest tests/test_enhance_tray.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Update the manifest with `store.update` once per stage change and once per finished slide so a reader sees progress; compute everything for a slide outside the lock.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit:** `feat: enhance a tray into previews, flags and auto-accepted slides`.

### Task 7: BatchRunner: scanning, background enhancement, resume, re-scan

**Files:**
- Create: `src/opticfilm_linux/batch/events.py`, `src/opticfilm_linux/batch/runner.py`, `tests/test_runner.py`

**Interfaces:**
- Consumes: `Scanner`, `ScannerStatus`, `ScannerNotReady`, `ScanError` (Task 2), `BatchStore`, `enhance_tray` (Tasks 4, 6), `save_sweep`.
- Produces (`events.py`): frozen `TrayStageChanged(number: int, stage: TrayStage)`, `SlideUpdated(number: int, position: int)`, `TrayFailed(number: int, message: str)`; `BatchEvent = TrayStageChanged | SlideUpdated | TrayFailed`.
- Produces (`runner.py`): `class BatchRunner`: `__init__(self, store: BatchStore, scanner: Scanner)`; `events: queue.Queue[BatchEvent]`; `scan_tray(self) -> int` (raises `ScannerNotReady` unless `scanner.status() is ScannerStatus.READY`, without creating a tray; otherwise creates the tray `SCANNING` with the batch default mode, queues the scan job on the single scan worker and returns the tray number immediately); `rescan_tray(self, number: int) -> int` (same readiness check; the tray must be `REVIEW` or `FAILED`; it keeps its old sweep, sets `sweep_version` to the next number, resets `slides` and `error`, stage `SCANNING`); `resume(self) -> None` (runs `store.recover_interrupted()` and queues enhancement for every tray it returns); `wait_idle(self, timeout: float | None = None) -> bool`; `close(self) -> None` (waits for running jobs, then shuts both workers down).

Scan job (decided): `scanner.scan_holder(ScanSettings(dpi, mode, infrared))`, `save_sweep` into `sweep_tmp_dir`, `os.replace` it onto `sweep_dir`, stage `SCANNED`, then queue `enhance_tray` on the single enhancement worker. Any `ScanError` or `OSError` marks the tray `FAILED` with the message, removes the temporary folder, and emits `TrayFailed`. Every stage change emits `TrayStageChanged`.

- [ ] **Step 1: Write failing tests** (a `FakeScanner` over a 600 dpi synthetic sweep, batch defaults `dpi=600`): `test_scan_when_not_ready_raises_and_creates_no_tray` (`statuses=(NOT_READY,)`; `ScannerNotReady`; manifest has no trays); `test_scan_tray_ends_in_review_with_four_slides` (after `wait_idle`); `test_events_follow_the_stage_order` (SCANNING, SCANNED, ENHANCING, REVIEW for tray 1); `test_failed_scan_marks_failed_and_leaves_no_sweep` (`before_scan` raises `ScanError("jam")`: tray `FAILED`, error contains "jam", neither `sweep-1` nor `sweep-1.tmp` exists, a `TrayFailed` event arrived); `test_enhancement_runs_while_the_next_tray_scans` (`before_scan` blocks the second scan on a `threading.Event`: tray 1 reaches `REVIEW` while tray 2 is still `SCANNING`; after setting the event and `wait_idle`, both are `REVIEW`); `test_scan_tray_returns_without_waiting_for_the_scan` (returns while `before_scan` is still blocked); `test_resume_enhances_a_scanned_tray` (manifest with a `SCANNED` tray and a saved sweep, new runner, `resume()`, `wait_idle()`: `REVIEW`); `test_resume_fails_an_interrupted_scan` (manifest `SCANNING` plus a `.tmp` folder: `FAILED`, folder gone); `test_rescan_keeps_old_sweep_and_resets_slides` (after a first scan: `sweep-1` still on disk, `sweep-2` exists, slides re-created, owner settings reset); `test_rescan_of_a_tray_in_scanning_raises` (`BatchError`); `test_close_waits_for_running_jobs`.
- [ ] **Step 2: Run** `uv run pytest tests/test_runner.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** with two `ThreadPoolExecutor(max_workers=1)` instances; never call the scanner or enhancement on the caller's thread; `wait_idle` waits on both executors' pending futures.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS (run the runner tests three times in a row to catch timing flakiness: `uv run pytest tests/test_runner.py -q --count 3` if `pytest-repeat` is unavailable, loop three times in the shell).
- [ ] **Step 5: Commit:** `feat: batch runner with background enhancement, resume and re-scan`.

### Task 8: Review operations

**Files:**
- Create: `src/opticfilm_linux/batch/review.py`, `tests/test_review.py`
- Modify: `src/opticfilm_linux/batch/runner.py`

**Interfaces:**
- Consumes: `BatchStore`, model, `reenhance_slide`, `BatchRunner`.
- Produces (`review.py`): `class NoSuchSlide(LookupError)`; `set_slide_status(store: BatchStore, number: int, position: int, status: SlideStatus) -> None` (only `ACCEPTED` or `NEEDS_LOOK`, else `BatchError`; marks `owner_decided`); `apply_slide_settings(store: BatchStore, number: int, position: int, settings: SlideSettings) -> None` (marks `owner_decided`, keeps the status); `accept_all(store: BatchStore) -> None` (every non-blank slide of every `REVIEW` tray becomes `ACCEPTED`, `owner_decided=True`); `set_tray_mode(store: BatchStore, number: int, mode: Literal["positive", "negative"]) -> None`. All raise `NoSuchSlide` for an unknown tray or position and `RawRemoved` when the tray's raw is gone (except `set_slide_status` and `accept_all`).
- Adds to `BatchRunner`: `edit_slide(self, number: int, position: int, settings: SlideSettings) -> None` (applies the settings, then queues `reenhance_slide` on the enhancement worker, emitting `SlideUpdated`); `set_slide_status(...)`; `accept_all(self) -> None`; `set_tray_mode(self, number: int, mode) -> None` (updates the mode and queues `enhance_tray` for the whole tray, which respects `owner_decided`).

- [ ] **Step 1: Write failing tests:** `test_set_slide_status_marks_the_owner_decision`; `test_set_status_rejects_blank_and_pending` (`BatchError`); `test_apply_settings_keeps_status_and_marks_decision`; `test_accept_all_skips_blank_and_non_review_trays`; `test_unknown_tray_or_slide_raises_nosuchslide`; `test_edit_slide_rejects_removed_raw` (`RawRemoved`); runner: `test_edit_slide_re_enhances_in_the_background` (rotation 90 edit, `wait_idle()`: the after preview is rotated and a `SlideUpdated` event arrived); `test_tray_mode_change_re_enhances_but_keeps_owner_choices` (owner rejected one slide, then `set_tray_mode(1, "negative")`, `wait_idle()`: that slide still `NEEDS_LOOK`); `test_concurrent_edits_and_enhancement_never_corrupt_the_manifest` (8 threads editing different slides while a tray enhances; afterwards `BatchStore.open` parses the file and every edit is present).
- [ ] **Step 2: Run** `uv run pytest tests/test_review.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** each operation as one `store.update`; the runner wrappers only add the queued re-enhancement and the event.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit:** `feat: review operations with owner choices that stick`.

### Task 9: Export and raw cleanup

**Files:**
- Create: `src/opticfilm_linux/batch/export.py`, `tests/test_export.py`

**Interfaces:**
- Consumes: `BatchStore`, `options_for`, `enhance_slide`, `split_slides`, `write_slide`, `OutputSpec`.
- Produces: `@dataclass(frozen=True) ExportResult(written: tuple[Path, ...])`; `export_batch(store: BatchStore, directory: Path, fmt: Format = "tiff16") -> ExportResult` (for every `REVIEW` or `EXPORTED` tray and every `ACCEPTED`, not-yet-`exported` slide: recompute the slide from the raw sweep and its stored settings, write it with `OutputSpec(directory, fmt, album=manifest.album, date=manifest.date)`, mark the slide `exported`; a tray becomes `EXPORTED` when all its non-blank slides are accepted and exported, otherwise it stays as it was); `cleanup_raw(store: BatchStore, number: int) -> int` (returns bytes freed; raises `CleanupRefused(BatchError)` unless the tray is `EXPORTED`; deletes only the tray's `sweep-*` folders, only if each resolves inside the batch directory and none is a symlink; sets `raw_removed=True`; leaves previews and the manifest).

- [ ] **Step 1: Write failing tests:** `test_export_writes_only_accepted_slides` (one `NEEDS_LOOK` slide: three files, tray stays `REVIEW`); `test_export_is_recomputed_from_raw_and_settings` (set `brightness_ev=1.0` on a slide: the exported pixels equal `enhance_slide` called directly with the same options, not the preview); `test_second_export_writes_nothing_new` (no duplicate files, same file list); `test_exported_tray_becomes_exported_when_everything_is_done`; `test_export_never_overwrites_existing_files` (pre-existing `<album>_<date>_0001.tif` keeps its bytes); `test_blank_slides_are_never_exported`; `test_cleanup_refused_for_a_tray_in_review` (`CleanupRefused`, sweep still on disk); `test_cleanup_frees_bytes_and_marks_raw_removed` (returns a positive size, sweep folders gone, previews and manifest remain); `test_cleanup_refuses_a_symlinked_sweep` (`sweep-1` replaced by a symlink to a folder outside the batch with a file in it: `CleanupRefused`, the outside file untouched); `test_cleanup_removes_all_sweep_versions` (after a re-scan, both `sweep-1` and `sweep-2` go).
- [ ] **Step 2: Run** `uv run pytest tests/test_export.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Check `path.is_symlink()` and `path.resolve().is_relative_to(store.directory.resolve())` before each `shutil.rmtree`.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit:** `feat: batch export and explicit raw cleanup`.

### Task 10: `ofscan batch` commands

**Files:**
- Create: `src/opticfilm_linux/batch/cli.py`, `tests/test_batch_cli.py`
- Modify: `src/opticfilm_linux/cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `batch.cli.add_batch_commands(commands: argparse._SubParsersAction) -> None` and `batch.cli.run_batch(args: argparse.Namespace) -> int`, wired into `cli.main` as `ofscan batch ...`: `new DIR --name NAME [--album A] [--date D] [--dpi N]`; `status DIR` (one line per tray: `tray 1  review  3 accepted, 1 needs a look, 0 blank`); `scan DIR --fake SWEEP_DIR [--count N]` (scans N trays with `FakeScanner`, waiting for each to finish enhancing; default 1); `accept-all DIR`; `export DIR --out OUT [--format tiff16|png8|jpeg]` (prints `N slides written`). Errors (`BatchError`, `ManifestFormatError`, `SweepFormatError`, `ScanError`, `OSError`) print `error: <message>` to stderr and return 2.

- [ ] **Step 1: Write failing tests:** `test_new_creates_a_batch` (exit 0, `batch.json` exists); `test_new_twice_gives_exit_2`; `test_status_lists_trays_after_a_scan` (`scan --count 2` then `status`: two `review` lines); `test_scan_fake_runs_to_review` (`--dpi 600` batch, the `synthetic_sweep_dir` fixture); `test_accept_all_then_export_writes_files` (stdout `4 slides written` per tray); `test_export_twice_writes_zero_new_files`; `test_corrupt_manifest_gives_exit_2_and_message` (garbage in `batch.json`: `error:` on stderr, no traceback); `test_missing_batch_gives_exit_2`.
- [ ] **Step 2: Run** `uv run pytest tests/test_batch_cli.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the commands with a `BatchRunner` created per invocation and closed in a `finally`.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`. Expected: PASS.
- [ ] **Step 5: Commit:** `feat: ofscan batch commands`.

### Task 11: Integration scenario, docs and gate

**Files:**
- Create: `tests/test_batch_scenario.py`
- Modify: `README.md`, `docs/CONTRIBUTING.md` (only if a rule changed)

**Interfaces:** none.

- [ ] **Step 1: Write the scenario test** `test_five_trays_with_crash_resume_edit_and_export`: a fake scanner reporting `NOT_READY` then `READY`; a caller that polls `status()` and calls `scan_tray()` five times (waiting until ready each time); after `wait_idle`, five trays are `REVIEW` with previews; simulate a crash by writing a manifest with tray 6 `SCANNING` plus a `.tmp` folder and tray 7 `ENHANCING`, reopen the batch with a new runner, `resume()`: tray 6 `FAILED` and clean, tray 7 `REVIEW`; edit one slide (`brightness_ev=1.0`), `accept_all()`, export twice: files written once, the edited slide's pixels differ from an unedited export; `cleanup_raw` on an exported tray frees bytes and a later `edit_slide` on it raises `RawRemoved`.
- [ ] **Step 2: Run** `uv run pytest tests/test_batch_scenario.py -q`. Expected: FAIL first only if something above is missing; fix the code, never the test, if it exposes a defect.
- [ ] **Step 3: Update `README.md`:** a "Batch scanning" section (what `ofscan batch` does, that the batch folder holds raw scans, about 1.3 GB per tray at 3600 dpi, that raw is only removed by the explicit cleanup, and that real scanning still waits on the driver).
- [ ] **Step 4: Run the full gate:** `uv run ruff check . && uv run ruff format --check . && uv run pytest --cov=opticfilm_linux --cov-fail-under=80 -q`. Expected: PASS, coverage at least 80 %; then `git ls-files | grep -i -E "local-research|\.pcap|scaninfo|\.icm"` prints nothing.
- [ ] **Step 5: Commit:** `docs: batch scanning in the README`. Then STOP: pushing and opening a PR happen only on the owner's explicit instruction.

## Self-review notes

- **Spec coverage:** goal 1 (batches, trays) Tasks 3, 4; 2 non-destructive Tasks 6, 9; 3 resumable Tasks 4, 7; 4 background enhancement and events Task 7; 5 status interface Task 2; 6 per-slide settings and strengths in the CLI Tasks 1, 8; 7 previews Tasks 5, 6; 8 flags Tasks 5, 6; 9 export Task 9; 10 `ofscan batch` Task 10. Anti-goals: no silent raw deletion (Task 9 only), temp-then-rename (Task 7), never blocking (Task 7), one manifest writer (Task 4). Auto-accept and "owner choice sticks" Tasks 6, 8.
- **Known judgement calls inside the plan:** `scan_tray()` returns immediately and scans on a worker (the spec's "blocks its own worker thread"); `RawRemoved` and `raw_removed` were added to make the cleanup safe; `export_batch` takes a folder and format rather than an `OutputSpec`.
- **Not covered by design:** the desktop app, packaging, crop/straighten, face-based auto-upright, red-eye, multi-exposure, device colour profile, the real "ready" signal.
