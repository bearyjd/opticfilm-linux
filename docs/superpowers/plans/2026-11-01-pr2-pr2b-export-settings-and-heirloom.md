# PR2 / PR2b Export Settings and Heirloom Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship remembered, size-aware manual exports at a new-batch default of 7200 dpi, then position-named heirloom JPEGs with embedded provenance.

**Architecture:** Keep immutable batch settings, tray assignments and export records in the manifest, with pure layout/provenance functions outside Qt. Reuse the manual controller/job/export path and exclusive-create writer; snapshot section identity when allocating a tray. Capture provenance around the existing scanner interface without changing driver commands or motor behavior.

**Tech Stack:** Python, pytest, NumPy, Pillow (EXIF, Lanczos, sRGB), PySide6 / Kirigami QML; existing JSON manifest and fake scanner.

**Spec:** `docs/superpowers/specs/2026-11-01-hands-free-scan-to-jpeg-design.md` (especially “Downstream: the heirloom contract”); companion `docs/superpowers/specs/2026-11-01-ux-workflow-plan.md` (PR2, constraints, test strategy). Read these in `/var/home/user/Documents/vibe-code/plustek-slide-scanner`; implementation targets the executing worktree.

## Global Constraints

- “Quality stays 95.”
- “7200 dpi is the default resolution” and “default full size”.
- “Scan order = slot order = event order.”
- “`<collection-root>/box-<N>/<section>/<NNN>.jpg`”
- “one letter `a`-`f` or a zero-padded number `01`-`99` (never three digits)”
- “the owner's carousels hold 140 slots” and “`NNN` is three digits so 140 fits”.
- “A frame detected as blank still leaves a gap rather than shifting later numbers”.
- “The scan or upload date is never the photo date.”
- “EXIF Orientation 1” and “The sRGB colour profile stays.”
- “Changing the scanner driver or any motor path.” is a non-goal.
- “Coverage omits `app/qt/*` (pyproject)” and “QML only binds.”
- Task scope overrides older companion wording: Lanczos, not INTER_AREA; box layout, not dated-event layout; `keep_raw` behavior and the auto-pipeline belong to PR4. No auto-export, auto-accept, raw cleanup, holder sequencing or theme work here.

## Review Focus

1. Old manifests with stored 600/3600/7200 dpi and absent new keys retain their resolution and round-trip (Task 1).
2. Odd, cropped or rotated dimensions and non-7200 sweeps use actual scan dpi, deterministic rounding and no accidental upscaling (Task 2).
3. Reused dialogs, switching batches and queued commands never leak another batch's destination or size (Task 3).
4. Partial trays, blank gaps, out-of-order export, rescan, collisions and hostile collection components retain identity and never overwrite (Tasks 4–6).
5. Old/missing/ambiguous diagnostics, XML-special text and owner-entered dates never invent provenance or populate heirloom-owned fields (Tasks 7–8).

---

## Delivery boundaries and repository map

Planning baseline: `docs/plan-pr2-pr2b`, HEAD equals local `origin/main` at `39062e7`; PR1's `keys` and `on_selection` export interfaces already exist here. The requested source checkout has older export signatures: preserve the worktree's PR1 behavior. This document is the only change now; no commits or pushes now.

At execution, branch PR2 as `feat/batch-export-settings-7200` from updated main after PR1/latch prerequisites; branch PR2b as `feat/heirloom-layout-and-provenance` from main after PR2 merges. Each PR must pass its own gates and remain usable manually. Commit instructions below apply only to future implementation.

Map: `batch/model.py` / `manifest.py` own records and compatibility; `batch/export.py` and `output.py` own rendering/writing; new `batch/layout.py` owns position allocation; new `provenance.py` owns metadata encoding; new `app/provenance.py` captures scan evidence. Existing controller/state/jobs own orchestration. Bridge/QML expose controls only. `store.py`, `review.py`, `enhance.py` retain their existing locking, acceptance and `options_for` behavior.

Shared types: `Format = Literal["tiff16", "png8", "jpeg"]` remains in `output.py`; add `JpegSize = Literal["full", "3600"]` there. `SlideKey = tuple[int, int]` remains the existing tray/slot key. Preserve existing optional/positional arguments; append new options as keyword-only except Qt slots, which require matching decorators and QML arguments.

## PR2 — `feat/batch-export-settings-7200` (3 tasks)

### Task 1: Persist export defaults without migrating old resolution

**Files:**
- Modify: `src/opticfilm_linux/batch/model.py`, `src/opticfilm_linux/batch/manifest.py`, `src/opticfilm_linux/app/prefs.py`, `src/opticfilm_linux/output.py`
- Test: `tests/test_manifest.py`, `tests/test_batch_model.py`, `tests/test_app_prefs.py`

**Interfaces:**
- Consumes: `from_json(text: str) -> BatchManifest`, `to_json(manifest: BatchManifest) -> str`, `BatchStore.create(directory: Path, name: str, album: str = "", date: str | None = None, defaults: BatchDefaults | None = None) -> BatchStore`.
- Produces: `BatchDefaults` adds `export_dir: str = ""`, `export_format: Format = "jpeg"`, `jpeg_size: JpegSize = "full"`; `BatchDefaults.dpi = 7200`, `prefs.DEFAULT_DPI = 7200`. No `auto_export` or `keep_raw` field yet.

- [ ] **Step 1: Write failing tests** `test_export_defaults_roundtrip`, `test_old_manifest_keeps_stored_dpi` (parameterize 600/3600/7200), `test_bad_export_defaults_rejected`. Use the existing `_manifest()` fixture builder and `dataclasses.replace`; remove only the three new keys to represent legacy JSON.
```python
assert BatchDefaults().dpi == DEFAULT_DPI == 7200
assert (BatchDefaults().export_dir, BatchDefaults().export_format, BatchDefaults().jpeg_size) == ("", "jpeg", "full")
assert from_json(legacy_json).defaults.dpi == stored_dpi
assert from_json(to_json(custom)).defaults == custom.defaults
assert not {"export_dir", "export_format", "jpeg_size"} & json.loads(to_json(_manifest()))["defaults"].keys()
with pytest.raises(ValueError):
    BatchDefaults(jpeg_size="half")
```
- [ ] **Step 2: Run to fail:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_manifest.py tests/test_batch_model.py tests/test_app_prefs.py -q` — new defaults/constructor assertions fail.
- [ ] **Step 3: Implement** `BatchDefaults.__post_init__(self) -> None` validation for format/size/string directory; retain orientation/slot checks. Extend `_defaults(data: dict[str, Any]) -> BatchDefaults` and `to_json(manifest: BatchManifest) -> str`: omit only default-valued new keys, like `slots`; always serialize dpi. For a legacy defaults object missing dpi, explicitly retain historical 3600; a stored dpi always wins. Do not change `ScanSettings`' driver-facing default.
- [ ] **Step 4: Run to pass:** repeat Step 2; add `tests/test_store.py` — PASS, including create/reopen at 7200 and explicit synthetic 600.
- [ ] **Step 5: Commit:** `git commit -m "feat: persist batch export settings and default new batches to 7200"` after staging only this task's files.

### Task 2: Deliver full or 3600-size JPEGs using actual sweep dpi

**Files:**
- Modify: `src/opticfilm_linux/output.py`, `src/opticfilm_linux/batch/export.py`
- Test: `tests/test_output.py`, `tests/test_export_reexport.py`

**Interfaces:**
- Consumes: `load_sweep(directory: Path) -> RawSweep`, `options_for(defaults: BatchDefaults, mode: Mode, settings: SlideSettings, position: int) -> ProcessOptions`, `write_slide(rgb: np.ndarray, spec: OutputSpec) -> Path`.
- Produces: `OutputSpec` adds `scan_dpi: int | None = None`, `jpeg_size: JpegSize = "full"`; `jpeg_geometry(width: int, height: int, scan_dpi: int, size: JpegSize) -> tuple[int, int, int]` returns width, height, delivered dpi. Existing writer signature and default TIFF behavior stay compatible.

- [ ] **Step 1: Write failing tests** `test_jpeg_geometry_uses_sweep_dpi`, `test_jpeg_lanczos_and_resolution`, `test_non_jpeg_pixels_unchanged`. Parameterize crop/rotation-swapped shapes, odd sizes, full/3600 and dpi 600/3600/7200; export-level test deliberately disagrees with batch dpi.
```python
assert jpeg_geometry(9600, 6500, 7200, "3600") == (4800, 3250, 3600)
assert jpeg_geometry(101, 99, 7200, "3600") == (51, 50, 3600)
assert jpeg_geometry(99, 101, 3600, "3600") == (99, 101, 3600)
assert jpeg_geometry(99, 101, 600, "3600") == (99, 101, 600)
assert jpeg_geometry(9600, 6500, 7200, "full") == (9600, 6500, 7200)
assert image.size == expected_size
assert image.getexif()[282] == image.getexif()[283] == delivered_dpi
assert image.getexif()[296] == 2
assert image.info["icc_profile"]
```
- [ ] **Step 2: Run to fail:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_output.py tests/test_export_reexport.py -q` — missing geometry/settings fail.
- [ ] **Step 3: Implement** `jpeg_geometry(...)` and extend `_encode(rgb: np.ndarray, fmt: str, handle: BinaryIO, *, scan_dpi: int | None = None, jpeg_size: JpegSize = "full") -> None`. For JPEG use divisor `max(1, scan_dpi / 3600)` only for size `3600`; dimensions are `max(1, floor(side / divisor + 0.5))`. Convert uint16 with existing `_to_8bit`, resize once with `Image.Resampling.LANCZOS`, then encode quality 95 with ICC and EXIF resolution. Tests compare decoded pixels with an independently Lanczos-resized/quality-95-encoded reference. Full and non-JPEG pixels retain the existing path; no fixed aperture dimensions.
  In `_export_tray`, replace the spec with actual `sweep.dpi` and selected size; preserve `keys`, `on_selection`, cancellation, `_mark_exported` stale guards and cleanup behavior. Invalid/nonpositive dpi fails before opening output; `scan_dpi=None` preserves legacy direct writer calls, but batch JPEG exports must supply it.
- [ ] **Step 4: Run to pass:** repeat Step 2 plus `tests/test_export.py tests/test_export_progress.py tests/test_output_pinned.py` — PASS; synthetic arrays keep memory bounded.
- [ ] **Step 5: Commit:** `git commit -m "feat: add Lanczos JPEG sizing and delivered resolution"` after staging task files.

### Task 3: Remember settings per batch and bind the existing UI

**Files:**
- Modify: `src/opticfilm_linux/app/controller.py`, `src/opticfilm_linux/app/state.py`, `src/opticfilm_linux/app/jobs.py`, `src/opticfilm_linux/app/qt/bridge.py`, `src/opticfilm_linux/app/qml/StartPage.qml`, `src/opticfilm_linux/app/qml/ExportDialog.qml`
- Test: create `tests/test_app_export_settings.py`; modify `tests/test_app_jobs.py`, `tests/qt/test_qt_smoke.py`

**Interfaces:**
- Consumes: Task 1 defaults, Task 2 writer; existing `run_export(store, directory, fmt, cancel, publish, keys=None) -> None` and controller session-aware writer queue.
- Produces: `Controller.set_export_settings(self, directory: Path, fmt: Format, jpeg_size: JpegSize) -> None`; `Controller.export(self, directory: Path, fmt: Format, jpeg_size: JpegSize = "full") -> None`. Add keyword-only `jpeg_size: JpegSize = "full"` to `run_export` and `export_batch`, preserving every PR1 argument. Export saves settings and captures them for the queued job in one session-bound command.
- Produces: `AppState.export_dir: str = ""`, `export_format: Format = "jpeg"`, `jpeg_size: JpegSize = "full"`; bridge `batchExportFolder`, `batchExportFormat`, `batchJpegSize` properties, `startExport(self, folder_url: str, fmt: str, jpeg_size: str = "full") -> None` with both two- and three-argument Qt slot overloads. Append keyword-only `export_dir: str = ""`, `export_format: Format = "jpeg"`, `jpeg_size: JpegSize = "full"` to `Controller.new_batch`; bridge `newBatch` appends the corresponding three optional positional values, with matching Qt slot overloads for old callers. Test both startExport overloads through QML, not just Python.

- [ ] **Step 1: Write failing tests** `test_batch_settings_survive_reopen_and_switch`, `test_queued_settings_remain_session_bound`, `test_export_dialog_reopens_from_batch`, `test_start_page_defaults_to_7200`. Use `open_new`, `wait_until`, `MemoryPrefs`, and Qt `loaded`; set different folder/format/size in two batches, close/reopen each, and invoke real QML controls by objectName.
```python
assert (state.export_dir, state.export_format, state.jpeg_size) == (str(out_a), "jpeg", "3600")
assert reopened.defaults.jpeg_size == "3600"
assert untouched_batch.defaults.export_dir == str(out_b)
assert dialog.property("folderUrl") == expected_url
assert bridge.batchExportFormat == "jpeg"  # even with legacy global tiff16 pref
assert resolution_control.property("currentValue") == 7200  # real mode; demo remains 600
assert not warnings
```
- [ ] **Step 2: Run to fail:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_app_export_settings.py tests/test_app_jobs.py -q`; `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest tests/qt/test_qt_smoke.py -m qt -q` — missing state/slots fail.
- [ ] **Step 3: Implement** the signatures above; `build_state` projects manifest values. Persist using `store.update` on the existing writer queue, never mutate a new session via an old queued command; reject readonly/inside-batch destinations before persistence. Explicit export defaults come from the batch, not legacy global export prefs. StartPage adds folder and full/3600 controls and selects 7200 in real mode; preserve demo dpi, orientation and slots. ExportDialog resets all three fields in `onOpened` and on batch replacement, and enables size only for JPEG; add `exportSize` and `newBatchDpi` objectNames. No visual restyle or estimates subsystem.
- [ ] **Step 4: Run to pass:** repeat Step 2, then the PR gates below — PASS; PR2 manually scans, accepts and exports with remembered settings and no automation.
- [ ] **Step 5: Commit:** `git commit -m "feat: bind batch export settings to start and export dialogs"` after staging task files.

## PR2b — `feat/heirloom-layout-and-provenance` (5 tasks)

### Task 4: Persist immutable section assignments and scanned positions

**Files:**
- Create: `src/opticfilm_linux/batch/layout.py`, `tests/test_batch_layout.py`
- Modify: `src/opticfilm_linux/batch/model.py`, `src/opticfilm_linux/batch/manifest.py`, `src/opticfilm_linux/batch/runner.py`; test `tests/test_manifest.py`, `tests/test_runner.py`

**Interfaces:**
- Consumes: `BatchStore.update(change: Callable[[BatchManifest], BatchManifest]) -> BatchManifest`; runner `scan_tray(self) -> int`, `rescan_tray(self, number: int) -> int`.
- Produces: `BatchManifest.collection: str = ""`, `box: int = 1`, `section: str = "a"`, `section_size: int = 140`; empty collection explicitly retains legacy layout. New frozen `TrayLocation(collection: str, box: int, section: str, start: int, capacity: int)` and `TrayRecord.location: TrayLocation | None = None`. `start` is the tray's first section position, `capacity` the holder's 4 or 6 slots.
- Produces: `allocate_location(manifest: BatchManifest, capacity: int) -> TrayLocation | None`; `slide_number(location: TrayLocation, position: int) -> int`; `section_directory(root: Path, location: TrayLocation) -> Path` in `batch/layout.py`.

- [ ] **Step 1: Write failing tests** `test_positions_are_load_order_not_export_order`, `test_partial_blank_and_rescan_preserve_positions`, `test_layout_validation_and_legacy_roundtrip`.
```python
assert slide_number(TrayLocation("family", 2, "a", 5, 4), 3) == 7
assert section_directory(tmp_path, location) == tmp_path / "family" / "box-2" / "a"
assert rescanned.location == original.location
assert allocated_after_blank.start == original.location.start + original.location.capacity
assert from_json(old_json).trays[0].location is None
with pytest.raises(ValueError):
    TrayLocation("../family", 1, "001", 1, 4)
```
- [ ] **Step 2: Run to fail:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_batch_layout.py tests/test_manifest.py tests/test_runner.py -q` — missing fields/functions fail.
- [ ] **Step 3: Implement** the interfaces above. In the atomic tray-add callback, allocate `start = 1 + max(start + capacity - 1)` among previous locations matching the active collection/box/section (empty maximum is zero). Reserve every holder position, including blanks and a partial final holder; never count accepted/exported slides. The existing driver is fixed to slide4 (`SLIDES="1-4"`), so app allocation uses capacity 4; pure helpers also test capacity 6 without adding strip scanning. Retain assignment across rescan. Validate single-component collection (no separators, dot components or NUL), exact integer box >=1 (reject bool), sections `[a-f]|0[1-9]|[1-9][0-9]`, section_size 1..999, positions 1..capacity. Reject new trays beyond section_size; a partial last holder may contain blank positions beyond the limit, but an accepted slide beyond it must fail export with an actionable message. Read absent fields with defaults; omit new default/None values on write, retain explicit location forever. Existing trays without locations stay legacy; never silently renumber them.
- [ ] **Step 4: Run to pass:** repeat Step 2 — PASS, including section_size 10 and 140, section 99/f, strip6 and reopening.
- [ ] **Step 5: Commit:** `git commit -m "feat: persist heirloom section positions when allocating trays"` after staging task files.

### Task 5: Write position filenames and retain export identity

**Files:**
- Modify: `src/opticfilm_linux/output.py`, `src/opticfilm_linux/batch/export.py`, `src/opticfilm_linux/batch/model.py`, `src/opticfilm_linux/batch/manifest.py`
- Test: create `tests/test_export_heirloom.py`; modify `tests/test_output.py`

**Interfaces:**
- Consumes: Task 4 location helpers, PR1 `clear_exported(store: BatchStore, keys: frozenset[SlideKey] | None = None) -> None`.
- Produces: `OutputSpec.position: int | None = None` enables three-digit position filenames; existing writer returns the actual path. New frozen `ExportRecord(path: str, width: int, height: int, scan_dpi: int, delivered_dpi: int, scan_token: str)`; `SlideRecord.exports: tuple[ExportRecord, ...] = ()`. Paths are relative to export_dir; append history only when the existing stale guards succeed.

- [ ] **Step 1: Write failing tests** `test_heirloom_positions_leave_blank_gaps`, `test_reexport_collision_is_exclusive`, `test_encode_failure_and_symlink_cannot_escape`. Accept slots 1/3/4, export tray 2 before tray 1 using PR1 keys; force a competing exclusive-create collision and an encoder error.
```python
assert {p.name for p in first.written} == {"001.jpg", "003.jpg", "004.jpg"}
assert first.written[0].parent == out / "family" / "box-1" / "a"
assert original.read_bytes() == original_bytes
assert reexport.written[0].name == "141.jpg"  # first repeat of position 1, section_size 140
assert slide.exports[-1].scan_token == "T001S1"
assert not partial.exists()
with pytest.raises(BatchError):
    export_batch(store_with_symlinked_section, out, "jpeg")
```
- [ ] **Step 2: Run to fail:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_export_heirloom.py tests/test_output.py -q` — legacy naming fails.
- [ ] **Step 3: Implement** `write_slide(rgb: np.ndarray, spec: OutputSpec) -> Path` position mode using exact initial `NNN`; add `OutputSpec.sequence_stride: int = 1`. For heirloom set stride=section_size: collisions advance by that stride, reserving 001..section_size for physical positions; refuse >999 before create. Keep `open(path, "xb")`, collision retry and unlink-on-encode-failure. Legacy mode retains four-digit album/date naming. Apply section layout to all chosen formats with their existing extension; JPEG is the default. Resolve the final parent against export root and reject symlink components/inside-batch targets before mkdir/open. Extend `_mark_exported(..., defaults: BatchDefaults, *, record: ExportRecord | None = None) -> Literal["marked", "stale_slide", "stale_tray"]` to append successful records with existing version/mode/settings guards. Keep PR1 skip/freed behavior; do not call cleanup.
- [ ] **Step 4: Run to pass:** repeat Step 2 plus `tests/test_export_reexport.py tests/test_export_progress.py` — PASS, including collision 999 exhaustion, old exports and stale renders.
- [ ] **Step 5: Commit:** `git commit -m "feat: write heirloom position paths without overwriting"` after staging task files.

### Task 6: Advance section or box and require a fresh tray

**Files:**
- Modify: `src/opticfilm_linux/batch/layout.py`, `src/opticfilm_linux/batch/runner.py`, `src/opticfilm_linux/batch/store.py`, `src/opticfilm_linux/app/controller.py`, `src/opticfilm_linux/app/state.py`, `src/opticfilm_linux/app/qt/bridge.py`, `src/opticfilm_linux/app/qml/StartPage.qml`, `src/opticfilm_linux/app/qml/MainPage.qml`
- Test: `tests/test_batch_layout.py`, `tests/test_runner.py`; create `tests/test_app_sections.py`; modify `tests/qt/test_qt_smoke.py`

**Interfaces:**
- Consumes: Task 4 manifest fields/assignment; existing `_run_writer`, scan/holder refusal rules.
- Produces: `advance_section(store: BatchStore, *, next_box: bool) -> None`; `Controller.finish_box(self, next_box: bool = True) -> None`. Append keyword-only `collection: str = ""`, `box: int = 1`, `section: str = "a"`, `section_size: int = 140` to `Controller.new_batch` and `BatchStore.create`; bridge `newBatch` appends the same optional positional values and preserves older Qt overloads. `AppState.collection: str = ""`, `box: int = 1`, `section: str = "a"`, `section_size: int = 140`, `can_finish_box: bool = False`; bridge `finishBox(self, next_box: bool) -> None` slot and matching manifest-derived properties.

- [ ] **Step 1: Write failing tests** `test_finish_section_allocates_fresh_tray_after_reopen`, `test_finish_refused_during_scan_move_or_job`, `test_section_rollover_and_old_tray_export`, `test_section_controls_bind`. Create two trays with fake scanner; transition, close/reopen, then scan; parameterize a/f/01/99.
```python
assert (after.box, after.section) == (before.box, "b")  # next_box=False from a
assert new_tray.number > old_tray.number and new_tray.location.start == 1
assert old_tray.location == saved_location
assert after_finish_box.box == before.box + 1 and after_finish_box.section == "a"
assert refused_snapshot == before_snapshot  # scan, holder move, job, readonly
assert old_tray_path.parent.name == "a"
assert newly_created.collection == "family"  # create while another batch is open
with pytest.raises(BatchError):
    runner.rescan_tray(closed_section_tray.number)
assert not warnings
```
- [ ] **Step 2: Run to fail:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_app_sections.py tests/test_batch_layout.py -q`; run Qt smoke command from Task 3 — missing actions fail.
- [ ] **Step 3: Implement** the exact interfaces above. Next section increments within the current alphabetic/numeric scheme; f/99 raises an actionable “finish box” refusal. Finish box increments box and resets to a/01 respectively. Serialize transition against pending writers; refuse during scan, enhancement, holder move or long jobs and in readonly mode, with unchanged manifest. Clear selected tray; disallow rescan of a tray assigned to a closed section in both controller and `BatchRunner.rescan_tray`, including after reopen; `scan()` continues to append through runner. No load/eject call. StartPage collection/box/section/size inputs travel atomically with new_batch through BatchStore.create; never queue settings against the session being replaced. MainPage offers separate “Next section” and “Finish box” actions. No automatic capacity rollover.
- [ ] **Step 4: Run to pass:** repeat Step 2 plus `tests/test_app_controller_exclusive.py tests/test_app_holder_export_lock.py` — PASS, existing exclusivity unchanged.
- [ ] **Step 5: Commit:** `git commit -m "feat: advance heirloom sections with fresh tray boundaries"` after staging task files.

### Task 7: Capture trustworthy scan evidence without changing the driver

**Files:**
- Create: `src/opticfilm_linux/provenance.py`, `src/opticfilm_linux/app/provenance.py`, `tests/test_scan_provenance.py`
- Modify: `src/opticfilm_linux/model.py`, `src/opticfilm_linux/sweepstore.py`, `src/opticfilm_linux/batch/model.py`, `src/opticfilm_linux/batch/manifest.py`, `src/opticfilm_linux/batch/runner.py`, `src/opticfilm_linux/app/qt/app.py`
- Test: `tests/test_sweepstore.py`, `tests/test_manifest.py`

**Interfaces:**
- Consumes: existing `Scanner.scan_holder(settings: ScanSettings) -> RawSweep`; driver already retains `*.diag.json` under `DriverConfig.log_dir`, but RawSweep currently contains only pixels/dpi/holder.
- Produces: frozen `ScanEvidence(digitized_at: str, duration_s: float, software: str, driver_revision: str | None, diagnostics: tuple[str, ...])` in `provenance.py`; `RawSweep.evidence: ScanEvidence | None = None`, `TrayRecord.evidence: ScanEvidence | None = None`. Diagnostics are captured JSON strings indexed by frame order (exactly four for slide4, `"{}"` placeholder for each unknown slot), retained in the manifest; no file-path dependency after capture.
- Produces: `capture_scan(scanner: Scanner, settings: ScanSettings, *, log_dir: Path | None, driver_dir: Path | None) -> RawSweep` in `app/provenance.py`; `ProvenanceScanner(scanner: Scanner, *, log_dir: Path | None, driver_dir: Path | None)` delegates the Scanner protocol unchanged, using capture_scan only for scanning.

- [ ] **Step 1: Write failing tests** `test_capture_uses_scan_time_not_export_time`, `test_diagnostics_are_scan_specific`, `test_missing_diagnostics_are_unknown`, `test_evidence_survives_sweep_and_manifest_roundtrip`, `test_wrapper_preserves_optional_capabilities`. Fake scan writes actual `YYYYmmdd-HHMMSS-tray-fN.diag.json` filenames; include stale/unrelated files, same-second replacements, malformed JSON and ambiguous duplicate frame matches; inject clocks with monkeypatch. Wrap both minimal and holder/status-reading-capable scanners.
```python
assert captured.evidence.digitized_at == "2026-11-01T12:00:00+00:00"
assert captured.evidence.duration_s == 827.0
assert len(captured.evidence.diagnostics) == 4
assert old_sweep.evidence is None
assert reopened.trays[0].evidence == captured.evidence
assert missing.evidence.driver_revision is None
assert missing.evidence.diagnostics == ("{}",) * 4
assert isinstance(wrapped, HolderControl) == isinstance(original, HolderControl)
```
- [ ] **Step 2: Run to fail:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_scan_provenance.py tests/test_sweepstore.py tests/test_manifest.py -q` — evidence interface missing.
- [ ] **Step 3: Implement** capture_scan with UTC start time and monotonic elapsed around exactly one existing scan call; identify new/changed diagnostics by before/after file identity and frame number, never “latest file” guessing. Malformed/ambiguous entries become explicit unknowns with a warning. Software is package name/version; driver revision is local configured checkout HEAD when provable, otherwise unknown (never app revision). Wrapper delegates status/button/stop/holder behavior exactly; preserve runtime `HolderControl` capability checks, including absence on unsupported backends, using transparent attribute delegation. Wire only scanner_factory construction. Save/load optional evidence in sweep metadata and copy it to TrayRecord before SCANNED publication; rescans replace evidence, retain location. Keep provenance.py's model annotations under TYPE_CHECKING and imports in serialization local where needed to avoid a model/provenance import cycle. Fake/old scans remain readable. No edits to scanner/driver.py, scanner commands or motor paths.
- [ ] **Step 4: Run to pass:** repeat Step 2 plus `tests/test_real_scanner_wiring.py tests/test_runner.py` — PASS; wrapper delegation and old metadata covered.
- [ ] **Step 5: Commit:** `git commit -m "feat: retain scan-time provenance and diagnostic evidence"` after staging task files.

### Task 8: Embed EXIF/XMP, raw-region hash and durable export records

**Files:**
- Modify: `src/opticfilm_linux/provenance.py`, `src/opticfilm_linux/output.py`, `src/opticfilm_linux/batch/export.py`, `src/opticfilm_linux/batch/model.py`, `src/opticfilm_linux/batch/manifest.py`
- Create: `tests/test_jpeg_provenance.py`; test `tests/test_export_heirloom.py`, `tests/test_output.py`

**Interfaces:**
- Consumes: `Slide` from `split_slides(sweep)` before enhancement; Task 7 evidence, Task 5 ExportRecord, effective ProcessOptions from `options_for`.
- Produces: frozen `JpegMetadata(exif: bytes, xmp: bytes, raw_sha256: str)`; `raw_region_sha256(slide: Slide) -> str`; `jpeg_metadata(manifest: BatchManifest, tray: TrayRecord, record: SlideRecord, slide: Slide, options: ProcessOptions, scan_dpi: int, delivered_dpi: int) -> JpegMetadata`. `OutputSpec.metadata: JpegMetadata | None = None`; `ExportRecord.raw_sha256: str = ""`. Add `TONE_VERSION = "linear-black-point-v1"` in provenance.py, describing the merged tone implementation, without altering it.

- [ ] **Step 1: Write failing tests** `test_embedded_provenance_complete_and_escaped`, `test_heirloom_fields_empty_unless_owner_date`, `test_raw_hash_is_region_specific_and_stable`, `test_baked_rotation_orientation_one`. Parse actual EXIF and XMP APP1 bytes with Pillow/ElementTree; no checks limited to mocked save kwargs.
```python
assert exif[271] == "Plustek" and exif[272] == "OpticFilm 135i"
assert exif[274] == 1 and exif.get_ifd(34665)[36868] == "2026:11:01 12:00:00"
assert 36867 not in exif.get_ifd(34665) and 270 not in exif and 34853 not in exif
assert owner_exif.get_ifd(34665)[36867] == "1975:06:01 00:00:00"
assert xmp_fields["PhotoDateSource"] == "owner-entered"
assert xmp_fields["RawSHA256"] == raw_region_sha256(slide)
assert changed_region_hash != raw_region_sha256(slide)
assert decoded.info["icc_profile"] and decoded.size == expected_rotated_size
```
- [ ] **Step 2: Run to fail:** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_jpeg_provenance.py tests/test_export_heirloom.py -q` — metadata absent.
- [ ] **Step 3: Implement** the declared functions and extend Task 2 `_encode` with `metadata: JpegMetadata | None = None`. EXIF: Make/Model/Software, Orientation=1, X/YResolution delivered dpi, ResolutionUnit=2; DateTimeDigitized in Exif IFD, UTC OffsetTimeDigitized; DateTimeOriginal only from validated batch.date (`YYYY-MM-DD`, midnight, no invented timezone). Never set generic DateTime to scan time, description or GPS. Preserve current baked orientation; do not rotate twice.
  XMP uses prefix `ofscan`, namespace `https://opticfilm-linux.org/ns/scan/1.0/`, version 1. Fields: ScannerMake, ScannerModel, Software, DriverRevision, DigitizedAt, ScanDPI, DeliveredDPI, FilmMode, Holder, Batch, Collection, Box, Section, Tray, Slot, ScanToken, Settings, ToneVersion, ReviewFlags, CalibrationIntegrity, GainCodes, OffsetCodes, ScanDurationSeconds, RawSHA256; PhotoDateSource only for owner-entered date. Settings is canonical JSON of effective brightness/color/grain/sharpen/dust/rotation/mirror options; ReviewFlags includes existing immutable Flag values plus `blank` for BLANK and `needs_look` whenever flags are nonempty or current status is NEEDS_LOOK (enhance's existing rule). Acceptance does not erase these evidence flags. Diagnostic fields come from that frame's captured evidence; missing fields explicitly say unknown. For historic trays with no evidence, omit EXIF digitized time and write unknown in XMP, never substitute export time. Test every named field, not just a subset.
  Hash canonical pre-enhancement slide region RGB then IR: SHA256 over ASCII dtype/shape/channel framing plus row-wise little-endian uint16 bytes; absent IR has an explicit marker. Avoid a full-frame copy; hash changes with any channel pixel and ignores adjacent slides. Persist hash and actual filename/dimensions in each successful ExportRecord. XML-escape all text using ElementTree. Encode with Pillow EXIF and sRGB, then insert one standard XMP APP1 packet (`http://ns.adobe.com/xap/1.0/\0` + XML) immediately after JPEG SOI inside the same exclusively opened handle; reject APP1 payload >65533 bytes and remove partial output. No sidecar-only substitute; retain non-JPEG output behavior.
- [ ] **Step 4: Run to pass:** repeat Step 2 plus `tests/test_output.py tests/test_manifest.py tests/test_export_reexport.py`, then PR gates — PASS. Round-trip owner date, XML metacharacters/non-ASCII, missing/partial diagnostics, both sizes, negative film, flags after acceptance, rotations 90/180/270, and hash stability across re-export.
- [ ] **Step 5: Commit:** `git commit -m "feat: embed heirloom JPEG provenance and raw-region hashes"` after staging task files.

## Per-PR gates and decisions to confirm before implementation

Run `PYTHONPATH=src .venv/bin/python -m pytest -q -p no:cacheprovider --cov=opticfilm_linux`, `.venv/bin/ruff check`, `.venv/bin/ruff format --check`, and `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest tests/qt/test_qt_smoke.py -m qt -q`. Require green assertions and coverage; a skipped Qt suite does not verify QML. Run long gates in the background and collect the final exit status. Obtain a separate read-only review per PR; no hardware/motor experiment required here.

Open implementation questions (provisional choices above are explicit, not new owner decisions):
- Does a re-export use `position + k * section_size` (proposed, preserves reserved physical positions), or another numbered scheme? Plain “next free number” can steal an unexported slide's filename; three digits eventually exhaust. Re-export preserves physical identity in ScanToken/metadata/history while its new filename changes; test collisions in a partial final holder too.
- Confirm next-section at f/99 refuses and asks to finish the box; finish-box resets to a/01. May a closed section ever be explicitly reopened for rescan?
- Which driver diagnostic schema/revision is authoritative for calibration integrity and gain/offset codes? Existing RawSweep has none; approve explicit unknown for unavailable historic/ambiguous evidence. Confirm the proposed namespace and whether duration means whole tray or per-frame (proposal: measured tray duration, clearly labelled in schema).
- Is owner batch date always a complete YYYY-MM-DD? Proposal rejects partial dates rather than inventing month/day. Dated-event layout and heirloom-side relaxed section-size support remain outside these PRs.

## Self-review

Spec coverage: PR2 defaults/persistence/size/UI → Tasks 1–3; heirloom layout/position/transition → 4–6; every provenance field, date ownership, orientation/ICC and durable matching → 7–8. Raw deletion, auto queue, holder changes, dated events and heirloom changes are explicitly deferred. Step scan: every task has named assertions, failing command, exact interface, passing command and future commit. Type consistency: Format/JpegSize, location/evidence/metadata/record types flow through their declared producers; PR1 keys/on_selection remain intact. Review Focus: all five map to named owning tests. Proportion: eight tasks, compact signatures and only non-obvious algorithms; no implementation performed during planning.

## Decisions made by the assistant (2026-11-01), for the owner to check

These settle the open questions above; each is inside the approved design, not a new owner decision.
- Re-export numbering: `position + k * section_size` (k = 1, 2, ...), capped at 999; the old file stays until the owner deletes it.
- Section rollover: next-section at `f` or `99` refuses and asks to finish the box; finish-box resets to `a`/`01`. Reopening a closed section is out of scope here.
- Provenance source: the driver's own `.diag.json` (calibration integrity, gain/offset codes, per-frame `total_seconds`) is authoritative; unavailable or historic scans record explicit "unknown", never invented values. Duration is stored per frame and as the measured whole tray, both labelled.
- Owner dates: accept YYYY, YYYY-MM or YYYY-MM-DD. EXIF DateTimeOriginal only for a full date; a partial date is stored in XMP with its precision. Nothing is rejected or invented.
- Compact numbering (owner, 2026-11-01): only the order matters; no carousel-slot field; a frame detected as blank still leaves a gap.
