# Desktop App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One live Kirigami window (`opticfilm`) that drives a `BatchRunner` over a `BatchStore`: scan when the scanner is ready, follow trays through their stages, review slides, export, and free disk space; runnable with no hardware via `opticfilm --fake SWEEP_DIR`.

**Architecture:** Three layers in `opticfilm_linux.app`. A Qt-free `Controller` owns the store, runner and scanner, does all core calls on worker threads, and publishes immutable `AppState` snapshots. A thin Qt bridge (`app/qt/`) mirrors the snapshot into properties and list models, drives a timer, and serves preview images. QML pages (Kirigami) hold layout only.

**Tech Stack:** Python 3.11+, existing `opticfilm_linux.batch` core, PySide6 + Kirigami from the distribution (never PyPI, never a declared dependency), pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-10-22-desktop-app-design.md` (reference layout: `../epson/src/fastfoto_linux/gui/`).

## Global Constraints

- Python 3.11+, `uv`, GPL-3.0-or-later; frozen dataclasses for all state; functions under 50 lines, files under 400 lines (800 max); type annotations on every signature.
- PySide6/Kirigami are NOT in `pyproject.toml` dependencies; `ofscan`, `opticfilm_linux.app` (except `app/qt/`) and all default tests import and run without them.
- Nothing outside `opticfilm_linux/app/qt/` imports PySide6. Only the controller touches the core, and only from worker threads; the UI thread never loads sweeps or 16-bit images.
- Tests first (RED, then GREEN). Default gate: `uv run ruff check . && uv run ruff format --check . && uv run pytest --cov=opticfilm_linux --cov-fail-under=80`. Qt layer is excluded from coverage and tested under marker `qt` (excluded from the default run).
- Application id `io.github.bearyjd.OpticFilmLinux`, display name "OpticFilm Linux", command `opticfilm`. English only.
- Scan button enabled only when scanner status is `ready`, no scan is pending, and the batch is writable. Destructive actions (re-scan, free disk space) ask first and say what they do.
- Failures appear as a banner with a plain message and technical detail one click away; never only logged.
- No scanner commands are sent from this project. v1 is slides (positive) only; new batches use `positive`.
- Every change goes on a branch and PR; never commit or push on main; push/PR only when the owner asks.

## Review Focus

Failure modes the spec implies but no task's tests would otherwise exercise (each is pinned by a test named in the owning task):

1. **Broken or foreign folder opened as a batch** (missing manifest, invalid manifest): start page shows a plain message, no traceback, no crash. (Task 5)
2. **Scanner `status()` raises or hangs**: Scan stays disabled, status shows "error", the UI-thread `tick()` still returns immediately. (Task 4)
3. **Slider spam / edit on a tray whose raw scans were removed**: only the last value is applied; `RawRemoved` becomes a banner, state stays consistent. (Tasks 6, 9)
4. **Export to a missing or unwritable folder, or cancelled mid-way**: banner, tray stages unchanged, files already written keep their exported marks, next export continues. (Tasks 1, 7)
5. **Empty batch (zero trays) and read-only batch**: selecting, accept all, export, free space are no-ops with a plain message; every write action is disabled read-only. (Tasks 5, 6, 7)

---

## File Structure

Create (all under `src/opticfilm_linux/app/` unless noted):

- `__init__.py` — package marker.
- `messages.py` — plain-words text: stage words, flag sentences, scanner status text. Pure functions.
- `state.py` — frozen snapshot dataclasses and `build_state(...)`.
- `prefs.py` — `Prefs` protocol, `MemoryPrefs`, recent-batches list logic.
- `controller.py` — `Controller` (lifecycle, polling, scan, selection, review actions).
- `jobs.py` — export and free-space job runners used by the controller.
- `qt/__init__.py`, `qt/app.py` (entry, argparse, `main()`), `qt/bridge.py`, `qt/models.py`, `qt/images.py`, `qt/settings.py` (QSettings-backed `Prefs`).
- `qml/*.qml` — `Main`, `StartPage`, `MainPage`, `StatusBar`, `TrayQueue`, `ReviewPane`, `SlideCard`, `AdjustPanel`, `ExportDialog`, `FreeSpaceDialog`, `ConfirmDialog`, `ErrorBanner`.
- Tests (flat, like the repo): `tests/test_app_messages.py`, `test_app_state.py`, `test_app_prefs.py`, `test_app_controller_*.py`, `test_app_jobs.py`, `test_export_progress.py`, `test_runner_stop.py`, `tests/qt/test_qt_smoke.py`.

Modify: `batch/export.py`, `batch/runner.py`, `sweepstore.py`, `pyproject.toml`, `.github/workflows/ci.yml`, `README.md`.

---

### Task 1: Export and cleanup progress, cancel, bulk free-space (core)

**Files:**
- Modify: `src/opticfilm_linux/batch/export.py`
- Test: `tests/test_export_progress.py`

**Interfaces:**
- Consumes: existing `export_batch(store, directory, fmt)`, `cleanup_raw(store, number) -> int`, `CleanupRefused`.
- Produces (later tasks rely on these exact names):
  - `@dataclass(frozen=True) class ExportProgress: done: int; total: int` (slides).
  - `ExportResult` gains `cancelled: bool = False`.
  - `export_batch(store, directory, fmt="tiff16", on_progress: Callable[[ExportProgress], None] | None = None, cancel: threading.Event | None = None) -> ExportResult` — total = accepted, not-yet-exported slides in REVIEW/EXPORTED trays that are not `raw_removed`; `cancel` is checked before each slide; `on_progress` is called after each written slide and once with `done=0` at the start.
  - `@dataclass(frozen=True) class FreeableTray: number: int; bytes: int` and `freeable_trays(store) -> tuple[FreeableTray, ...]` — EXPORTED trays with `raw_removed` False, size = sum of `sweep-*` folder sizes.
  - `free_space(store, on_progress: Callable[[int, int], None] | None = None, cancel: threading.Event | None = None) -> int` — runs `cleanup_raw` per freeable tray, checks `cancel` before each tray, calls `on_progress(done_trays, total_trays)` after each, returns bytes freed.

- [ ] **Step 1: Write failing tests** in `tests/test_export_progress.py` (build a batch with the `make_sweep`/`FakeScanner`/`BatchRunner` pattern used in `tests/test_export.py`; reuse its helper if one exists, else a local fixture): `test_export_reports_progress_per_slide` (calls end with `ExportProgress(done=N, total=N)`, total equals accepted count); `test_export_cancel_keeps_written_and_continues_later` (set the event from `on_progress` after 1 slide → `result.cancelled`, 1 file written, 1 slide `exported`; a second `export_batch` writes the rest, none twice); `test_export_to_unwritable_directory_raises_and_marks_nothing` (directory is a file path → exception, no slide `exported`); `test_freeable_trays_lists_only_fully_exported`; `test_free_space_cancel_stops_between_trays` (two exported trays, cancel after first → one `raw_removed`, one not); `test_free_space_returns_bytes_freed`.
- [ ] **Step 2: Run** `uv run pytest tests/test_export_progress.py -q` — expect FAIL (names missing).
- [ ] **Step 3: Implement** the signatures above in `export.py`. Count the total up front from the manifest; pass `cancel`/`on_progress` into `_export_tray`; keep `_mark_exported` after each write (existing guarantee). `freeable_trays` reuses `_checked_sweep_folders` and `_folder_size`.
- [ ] **Step 4: Run** `uv run pytest -q` — expect all PASS (existing export tests unchanged).
- [ ] **Step 5: Commit** `git commit -m "feat: export and cleanup progress, cancel and bulk free-space"`

### Task 2: Runner stop and public sweep dpi (core)

**Files:**
- Modify: `src/opticfilm_linux/batch/runner.py`, `src/opticfilm_linux/sweepstore.py`
- Test: `tests/test_runner_stop.py`, `tests/test_sweepstore.py`

**Interfaces:**
- Produces: `BatchRunner.stop() -> None` — drops jobs not yet started in both pools (`shutdown(wait=True, cancel_futures=True)`), waits for the job running now, and leaves cancelled trays in `SCANNED`/`ENHANCING` for `resume()` to pick up next time. `BatchRunner.scan_pending() -> bool` (True while a scan future is pending). `sweepstore.sweep_dpi(directory: Path) -> int` (public wrapper over `_load_meta`, raises `SweepFormatError`).

- [ ] **Step 1: Write failing tests:** `test_stop_drops_queued_enhancement_and_resume_requeues` (two trays scanned with a gated `before_scan`/slow enhance so tray 2's enhancement is queued; `stop()`; reopen store + new runner; `resume()`; `wait_idle()` → both trays `REVIEW`); `test_scan_pending_true_while_scanning`; `test_sweep_dpi_reads_meta_and_rejects_missing`.
- [ ] **Step 2: Run** the three tests — expect FAIL.
- [ ] **Step 3: Implement** as specified. `stop()` must be idempotent and safe after `close()`.
- [ ] **Step 4: Run** `uv run pytest -q` — all PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: runner stop for fast shutdown, scan_pending, public sweep_dpi"`

### Task 3: Messages and state snapshot (Qt-free)

**Files:**
- Create: `src/opticfilm_linux/app/__init__.py`, `app/messages.py`, `app/state.py`
- Test: `tests/test_app_messages.py`, `tests/test_app_state.py`

**Interfaces:**
- Produces in `messages.py`: `flag_text(flag: Flag) -> str` with exact copy `too_dark`→"Very dark", `too_bright`→"Very bright", `clipping`→"Highlights or shadows are cut off", `dust`→"Dust or scratches detected"; `stage_text(stage: TrayStage) -> str` (e.g. "Scanning", "Enhancing", "Ready to review", "Exported", "Failed"); `scanner_text(status: ScannerStatus) -> str` with `unknown`→"Scanner status unknown: scanning is not available yet", `ready`→"Scanner ready", `busy`→"Scanner busy", `not_ready`→"Scanner not ready: load a tray", `not_connected`→"Scanner not connected", `error`→"Scanner error".
- Produces in `state.py` (all `@dataclass(frozen=True)`):
  - `SlideCard(position:int, status:SlideStatus, flags:tuple[str,...] /*plain sentences*/, settings:SlideSettings, owner_decided:bool, exported:bool, updating:bool, preview_token:int /*after-preview mtime_ns, 0 if none*/)`
  - `TrayRow(number:int, stage:TrayStage, stage_text:str, progress:float|None, elapsed_s:float|None, error:str|None, needs_look:int, raw_removed:bool)`
  - `Banner(message:str, detail:str)`
  - `ExportView(running:bool, done:int, total:int, finished:bool, written:int, cancelled:bool)`
  - `FreeSpaceView(trays:tuple[tuple[int,int],...] /*(number, bytes)*/, total_bytes:int, running:bool, done:int, total:int, freed:int|None)`
  - `AppState(batch_name:str|None, readonly:bool, scanner:ScannerStatus, scanner_text:str, can_scan:bool, scan_pending:bool, trays:tuple[TrayRow,...], selected:int|None, slides:tuple[SlideCard,...], banner:Banner|None, export:ExportView|None, free_space:FreeSpaceView|None)`; `EMPTY_STATE: AppState` (no batch).
  - `build_state(manifest: BatchManifest|None, *, preview_tokens: Mapping[tuple[int,int],int], scanner: ScannerStatus, scan_pending: bool, readonly: bool, selected: int|None, updating: frozenset[tuple[int,int]], progress: Mapping[int,float], scan_started: Mapping[int,float], now: float, banner, export, free_space) -> AppState`.

- [ ] **Step 1: Write failing tests:** messages — exact strings above for all 4 flags and all 6 scanner statuses; state — `can_scan` is True only for `READY` + not pending + not readonly + batch present (parametrised over all combinations); `needs_look` counts slides with status `NEEDS_LOOK`; a selected tray that no longer exists falls back to `None`/no slides; `updating` marks only the listed slides; elapsed_s set only for SCANNING trays.
- [ ] **Step 2: Run** both files — FAIL.
- [ ] **Step 3: Implement** `messages.py` and `state.py`. `build_state` is a pure function; no I/O.
- [ ] **Step 4: Run** both files — PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(app): plain-words messages and immutable state snapshot"`

### Task 4: Controller core — lifecycle, polling, scan, selection

**Files:**
- Create: `src/opticfilm_linux/app/controller.py`
- Test: `tests/test_app_controller_core.py`

**Interfaces:**
- Consumes: Tasks 1–3 plus `BatchStore`, `BatchRunner`, `Scanner`, `FakeScanner`.
- Produces: `class Controller` with
  - `__init__(self, scanner_factory: Callable[[int], Scanner], clock: Callable[[], float] = time.monotonic)` — factory takes the batch dpi (demo mode returns a `FakeScanner`).
  - `state(self) -> AppState` (cached, O(1), never does I/O).
  - `tick(self) -> bool` — UI-thread safe, returns immediately: drains `runner.events` (updating-markers, banners from `TrayFailed`, dirtiness), submits at most one refresh job (scanner `status()` + manifest snapshot + preview mtimes + `build_state`) to a single status worker; returns True when `state()` changed since the last call. A `status()` exception becomes `ScannerStatus.ERROR` plus a banner.
  - `new_batch(directory: Path, name: str, album: str, date: str|None, dpi: int) -> None`, `open_batch(directory: Path, readonly: bool = False) -> None` (run on a command worker; `BatchError`/OSError → banner; a held lock → banner whose `detail` starts with `LOCKED:` so the UI can offer read-only), `close_batch() -> None`.
  - `scan() -> None`, `rescan(number: int) -> None`, `select_tray(number: int) -> None`; failures (`ScannerNotReady`, `BatchError`) → banner, never raised.
  - `dismiss_banner() -> None`, `preview_path(number: int, position: int, kind: Literal["before","after"]) -> Path|None`.
  - `shutdown_decision() -> Literal["ok","scan_running"]`, `shutdown(wait_for_scan: bool) -> None` (calls `runner.stop()`, cancels export, closes store).

- [ ] **Step 1: Write failing tests** (use `FakeScanner` with `synthetic.make_sweep(dpi=600)` and a poll helper `wait_until(lambda: controller.tick() and cond)` with a 10 s cap): `test_scan_five_trays_reach_review` (press scan as `can_scan` becomes true, five times); `test_scan_when_not_ready_sets_banner_not_raises` (statuses `[NOT_READY]`); `test_can_scan_false_while_scan_pending`; **`test_tick_returns_fast_when_scanner_status_blocks`** (status() sleeps 1 s → `tick()` and `state()` each take < 0.05 s); **`test_scanner_status_exception_shows_error`**; **`test_scan_returns_fast_when_scan_blocks`** (`before_scan` blocks on an Event → `scan()` < 0.05 s, `shutdown_decision()=="scan_running"`); `test_select_tray_fills_slides_with_plain_flag_sentences`; `test_open_batch_held_by_other_writer_reports_locked` (hold `BatchStore.open` in the test, then `open_batch`); `test_open_batch_readonly_disables_scan`; `test_open_missing_folder_gives_plain_banner` (**Review Focus 1**, also a folder with garbage `manifest.json`); `test_empty_batch_select_unknown_tray_is_noop`; `test_rescan_resets_slides_and_keeps_old_sweep`; `test_shutdown_resumes_next_time` (stop during enhancement, reopen, `resume` → review).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** `Controller`. Two single-thread executors: `command` (open/new/close/export-like work) and `status` (refresh). All mutable controller fields are replaced, never mutated, under one lock; `state()` returns the last built snapshot. Scan elapsed time uses `clock`. Keep the file < 400 lines by leaving review actions to Task 6 and jobs to Task 7 (add them as mixin-free methods in those tasks).
- [ ] **Step 4: Run** `uv run pytest tests/test_app_controller_core.py -q` — PASS; then `uv run pytest -q`.
- [ ] **Step 5: Commit** `git commit -m "feat(app): controller lifecycle, polling, scan and selection"`

### Task 5: Preferences and recent batches (Qt-free)

**Files:**
- Create: `src/opticfilm_linux/app/prefs.py`
- Test: `tests/test_app_prefs.py`

**Interfaces:**
- Produces: `class Prefs(Protocol): get(key: str, default: str|None=None) -> str|None; set(key: str, value: str) -> None`; `MemoryPrefs` (dict-backed); `RecentBatch(path: Path, exists: bool)` (frozen); `recent_batches(prefs: Prefs) -> tuple[RecentBatch, ...]` (most recent first, max 10, `exists` from the filesystem); `remember_batch(prefs, path) -> None` (moves to front, dedupes); `forget_batch(prefs, path) -> None`. Keys stored as JSON under `"recent_batches"`; also `DEFAULT_DPI = 3600`, `DPI_CHOICES = (600, 1200, 2400, 3600, 7200)`, `batch_subfolder(parent: Path, name: str) -> Path` (name sanitised: strips path separators and leading dots, empty → `"batch"`).

- [ ] **Step 1: Write failing tests:** dedupe + order + cap 10; missing path returned with `exists=False`; `forget_batch`; corrupt JSON in prefs → empty list (no crash); `batch_subfolder(Path("/x"), "../../etc")` stays inside `/x`; empty name.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(app): preferences and recent batches"`

### Task 6: Controller review actions

**Files:**
- Modify: `src/opticfilm_linux/app/controller.py`
- Test: `tests/test_app_controller_review.py`

**Interfaces:**
- Produces on `Controller`: `set_status(number, position, status: SlideStatus)`, `edit_slide(number, position, settings: SlideSettings)`, `rotate(number, position, quarter_turns: int)` (±1, builds new `SlideSettings` with `dataclasses.replace`, rotation modulo 360), `reset_slide(number, position)` (settings back to `SlideSettings()`), `accept_all()`. All run on the command worker, mark the slide `updating` until its `SlideUpdated` event arrives, convert any exception (`BatchError`, `RawRemoved`, `ValueError`, `NoSuchSlide`) to a banner, and are refused with a banner when the batch is read-only.

- [ ] **Step 1: Write failing tests:** `test_accept_all_accepts_non_blank_in_review_trays_only`; `test_edit_marks_owner_decided_and_preview_token_changes_but_status_kept`; `test_rotate_wraps_around` (270 + right → 0, 0 + left → 270); `test_reset_returns_to_auto`; **`test_fifty_rapid_edits_end_consistent`** (50 `edit_slide` calls with increasing brightness; after idle the stored brightness is the last and the slide is not `updating`); **`test_edit_after_raw_removed_gives_banner`** (**Review Focus 3**); `test_readonly_batch_refuses_every_write_action` (parametrised over the six actions plus `scan`/`rescan`); `test_accept_all_on_empty_batch_is_noop`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** (delegate to `runner.set_slide_status`, `runner.edit_slide`, `runner.accept_all`). **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(app): controller review actions"`

### Task 7: Controller export and free disk space

**Files:**
- Create: `src/opticfilm_linux/app/jobs.py`
- Modify: `src/opticfilm_linux/app/controller.py`
- Test: `tests/test_app_jobs.py`

**Interfaces:**
- Consumes: Task 1 `export_batch`, `freeable_trays`, `free_space`.
- Produces on `Controller`: `export(directory: Path, fmt: Format) -> None`, `cancel_export() -> None`, `dismiss_export() -> None`, `request_free_space() -> None` (fills `state.free_space` with candidates, `running=False`), `confirm_free_space() -> None`, `cancel_free_space() -> None`, `dismiss_free_space() -> None`. In `jobs.py`: `run_export(store, directory, fmt, cancel, publish: Callable[[ExportView], None]) -> None` and `run_free_space(store, cancel, publish: Callable[[FreeSpaceView], None]) -> None`, each catching exceptions into a final view plus a raised error the controller turns into a banner.

- [ ] **Step 1: Write failing tests:** `test_export_progress_reaches_total_and_finishes`; `test_export_cancel_marks_cancelled_and_second_export_continues`; **`test_export_to_missing_folder_gives_banner_and_changes_no_stage`** (**Review Focus 4**); `test_export_empty_batch_reports_zero_written`; `test_free_space_lists_only_fully_exported_trays_with_sizes`; `test_free_space_requires_confirm_before_deleting` (after `request_free_space` nothing removed; after `confirm_free_space` raw gone, previews and exported files still present); `test_cancel_free_space_stops_before_next_tray`; `test_readonly_refuses_export_and_free_space`; `test_shutdown_cancels_running_export`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** Export and free space run on the command worker; progress callbacks call `publish`, which swaps the controller's view under its lock.
- [ ] **Step 4: Run** — PASS; then full gate (`ruff check`, `ruff format --check`, `pytest --cov ... --cov-fail-under=80`) and confirm `app/` (excluding `qt/`) coverage ≥ 80 %.
- [ ] **Step 5: Commit** `git commit -m "feat(app): controller export and free disk space"`

### Task 8: Qt bridge, models, image provider, entry point

**Files:**
- Create: `src/opticfilm_linux/app/qt/{__init__,app,bridge,models,images,settings}.py`
- Modify: `pyproject.toml` (script `opticfilm = "opticfilm_linux.app.qt.app:main"`; marker `qt`; `addopts = "-m 'not hardware and not qt'"`; `[tool.coverage.run] omit = ["*/opticfilm_linux/app/qt/*"]`; ruff per-file ignore for `tests/qt/**`: `E402`)
- Test: covered by Task 10 (`qt` marker); no default-run test.

**Interfaces:**
- Consumes: `Controller`, `Prefs`, `app.prefs` helpers.
- Produces: `main(argv: list[str] | None = None) -> int` (args: `--fake SWEEP_DIR`; real mode builds `CommunityScanner`, whose status is `unknown`). `Bridge(QObject)` exposing properties mirroring `AppState` (`scannerText`, `canScan`, `scanPending`, `readonly`, `batchName`, `selected`, `banner*`, `export*`, `freeSpace*`, `demoDpi`) and slots `scan()`, `rescan(n)`, `selectTray(n)`, `accept(n,p)`, `needsLook(n,p)`, `rotate(n,p,dir)`, `setAdjust(n,p,brightness,color,grain,sharpen)` (**debounced 300 ms per slide via a `QTimer`, last value wins**), `resetSlide(n,p)`, `acceptAll()`, `newBatch(parentUrl,name,album,date,dpi)`, `openBatch(path, readonly)`, `recentBatches`, `forgetBatch(path)`, `startExport(url,fmt)`, `cancelExport()`, `requestFreeSpace()`, `confirmFreeSpace()`, `closeRequested() -> str`. `TrayModel` and `SlideModel` (`QAbstractListModel`, roles = `TrayRow`/`SlideCard` fields, updated by diff on `tick`). `PreviewProvider(QQuickImageProvider)` serving `image://preview/<tray>/<pos>/<kind>/<token>` from `controller.preview_path`, so a changed token busts QML's cache. A `QTimer` calls `controller.tick()` every 500 ms. `settings.py`: `QSettingsPrefs` implementing `Prefs`; window size and last export folder/format stored there too.

- [ ] **Step 1: Implement** following the shape of `../epson/src/fastfoto_linux/gui/qt/{app,bridge,photos_model}.py` (read those first for the exact PySide6 idioms; do not copy fastfoto logic). Without PySide6 installed, `python -c "import opticfilm_linux.app"` and the full default test run must still pass.
- [ ] **Step 2: Verify** (needs `dnf install python3-pyside6`, ask the owner first): `QT_QPA_PLATFORM=offscreen uv run --no-sync python -c "import opticfilm_linux.app.qt.app"` succeeds; `uv run pytest -q` (default run) still passes without PySide6.
- [ ] **Step 3: Commit** `git commit -m "feat(app): Qt bridge, list models, preview provider and entry point"`

### Task 9: QML pages

**Files:**
- Create: `src/opticfilm_linux/app/qml/*.qml` (list in File Structure); ensure `pyproject.toml` wheel config includes `*.qml` (hatch `include`).
- Test: Task 10.

**Interfaces:** QML calls only the bridge slots from Task 8; no logic beyond layout and bindings.

- [ ] **Step 1: Implement** per spec layout A. `Main.qml`: `Kirigami.ApplicationWindow` with a `StartPage` (new batch: parent folder, name, album, date, dpi from `DPI_CHOICES`, default 3600, locked to `demoDpi` in demo mode; recent list with greyed "not found" entries and a remove action; a "batch in use by another program" message with an "Open read-only" button when the banner detail starts with `LOCKED:`) and `MainPage`. `MainPage`: `StatusBar` (scanner text, Scan button bound to `canScan`, busy/elapsed text), `TrayQueue` (stage words, progress bar, "needs a look" count, rescan with `ConfirmDialog` saying the old scan is kept and slide settings reset), `ReviewPane` (four `SlideCard`s: after preview, before/after toggle, status badge, flag sentences, "updating" marker, "reset to auto"), `AdjustPanel` (brightness −2…2 EV, colour 0…1, grain, sharpen sliders, rotate left/right), `ErrorBanner` (message + "Details" expander), `ExportDialog` (folder, format tiff16/png8/jpeg, progress, cancel), `FreeSpaceDialog` (lists trays and sizes, states that only raw scans are removed, confirm). Keyboard shortcuts: `Left`/`Right` previous/next slide, `Space` toggle accept, `[` / `]` rotate. Read-only batch: all write controls `enabled: !bridge.readonly`. Closing: `onClosing` asks `closeRequested()` and shows a wait/keep-working dialog while a scan runs.
- [ ] **Step 2: Verify manually** in the Fedora container: `QT_QPA_PLATFORM=offscreen uv run --no-sync opticfilm --fake demo-sweep` loads without QML errors on stderr (smoke test automates this in Task 10).
- [ ] **Step 3: Commit** `git commit -m "feat(app): Kirigami pages"`

### Task 10: Qt smoke test, CI job, docs

**Files:**
- Create: `tests/qt/test_qt_smoke.py`
- Modify: `.github/workflows/ci.yml`, `README.md`, `docs/CONTRIBUTING.md`

**Interfaces:** Consumes the whole app; produces the optional CI job and user docs.

- [ ] **Step 1: Write the smoke test** (`@pytest.mark.qt`, `QT_QPA_PLATFORM=offscreen`, skipped via `pytest.importorskip("PySide6")`): load `Main.qml` with a `Bridge` over a `FakeScanner` and a 600 dpi synthetic sweep; create a batch; assert `canScan` becomes true; scan; wait for the tray to reach review; accept all; export to `tmp_path`; assert 4 files written (or the non-blank count) and no QML warnings captured by a message handler; one more test that a second `Bridge` on the same folder reports the locked message.
- [ ] **Step 2: Run** `QT_QPA_PLATFORM=offscreen uv run --no-sync pytest -m qt -q` in the Fedora container — PASS.
- [ ] **Step 3: CI:** add a second job `qt` to `ci.yml` (container `fedora:43`, `dnf install -y python3-pyside6 kf6-kirigami qt6-qtdeclarative uv`, then `uv sync` and the `-m qt` run); mark it `continue-on-error: false` but not required; the existing `test` job is unchanged.
- [ ] **Step 4: Docs:** README gets a "Desktop app" section (what it does, `opticfilm --fake SWEEP_DIR`, that PySide6/Kirigami come from the distribution, real scanning still waits on the driver); CONTRIBUTING gets the Qt dev setup (Fedora container, `dnf install python3-pyside6`, the `qt` marker).
- [ ] **Step 5: Final gate:** `uv run ruff check . && uv run ruff format --check . && uv run pytest --cov=opticfilm_linux --cov-fail-under=80`; commit `git commit -m "test: Qt smoke test, optional Fedora CI job, desktop app docs"`.

---

## Calls made without the owner (add to the spec's list)

- `cleanup_raw` stays per-tray; the bulk "free space" with progress and cancel is a new `free_space()` beside it (the spec's "cleanup_raw accepts a progress callback" is met by the wrapper).
- `BatchRunner.stop()` drops queued enhancement but lets the job running now finish (at most one tray's enhancement) before the window closes; the window says "finishing the current tray".
- New public `sweepstore.sweep_dpi()` so demo mode can lock the new-batch dpi to the fake sweep's dpi.
- `LOCKED:` prefix on a banner's detail is how the Qt-free controller tells the UI to offer read-only viewing.

## Self-review notes

- Spec coverage: goals 1–9 map to Tasks 4 (1, 2, 3, 7), 6 (4), 7 (5), 5+9 (6), 8–9 (8), 10 (9); non-goals untouched. Demo mode, QSettings prefs, banner failures, close handling, read-only, cancel semantics and the `qt` CI job each have a named task step.
- Type names checked across tasks: `ExportProgress`, `ExportResult.cancelled`, `FreeableTray`, `ExportView`, `FreeSpaceView`, `AppState`, `SlideCard`, `TrayRow` are defined once (Tasks 1, 3) and only consumed afterwards.
- Proportion: plan is deliberately signature-and-test level; bodies are left to the implementer.
