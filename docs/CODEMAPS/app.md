<!-- Generated: 2026-11-01 | Source: main @ 39062e7 | Files: app/ 9 Python + app/qt/ 6 Python + app/qml/ 13 QML = 28 | Token estimate: ~950 -->
# App layer (Qt-free controller + thin Qt shell)

Paths below are relative to `src/opticfilm_linux/`.

## Files (lines)
- `app/controller.py` 677: `Controller(HolderCommands)`; `tick() -> bool`, batch lifetime, review edits, worker coordination.
- `app/state.py` 204: frozen views; `build_state(...) -> AppState` derives buttons, banners, trays and slides.
- `app/status.py` 119: `StatusPoller`, off-thread status and `take_eject_press()`; press TTL 2 s.
- `app/holder.py` 171: `HolderCommands.eject_holder()`, `load_next_holder()`, `confirm_scanner_reset()`.
- `app/eject_watch.py` 173: `EjectWatch.yield_to_app() -> None`, `restore() -> str | None`.
- `app/jobs.py` 156: `run_export(...) -> None`, `free_space_candidates(store) -> FreeSpaceView`, `run_free_space(...) -> None`.
- `app/messages.py` 183: UI text, `load_command(driver_dir: Path | None, runner: str | None = None) -> str`.
- `app/prefs.py` 67: preferences/recent batches.
- `app/qt/app.py` 149: `main()`, scanner factory, `--fake SWEEP_DIR`, bridge/QML startup.
- `app/qt/bridge.py` 440: `Bridge` exposes state as QObject properties; slots dispatch controller commands.
- `app/qt/{images,models,settings}.py` 32/39/16: previews, list model, QSettings.
- `app/qml/` 13 files: main/start pages, tray/slide/adjustment views and dialogs; `ExportDialog.qml` 119, `LoadHelpDialog.qml` 112.

## Scanner ownership and holder controls
`Controller._attach(store, readonly)` -> `EjectWatch.yield_to_app()` -> scanner factory -> poller/runner/holder.
The watcher stops `opticfilm-eject-watch.service` if running, waits for it to settle and checks for surviving `of135i watch` processes; a live watcher prevents scanner opening. Batch replacement keeps it stopped. Detach stops jobs/poller/runner and closes the store; restoration requires idle holder/status work and restarts only a previously running watcher. Failures become banners; read-only batches open no scanner.

`DriverScanner.status_reading()` -> `StatusPoller` rising edge -> `Controller.tick()` -> `_act_on_eject_button(poller)` -> `eject_holder()`.
Presses are consumed once, dropped when disabled/busy/stale, and require `can_eject` plus fresh READY/NOT_READY status. Holder moves use a separate worker, refuse queued/running scans and export/free-space jobs, and suppress duplicate clicks. State also guards read-only, holder capability and interruption. Load-next additionally requires its experimental flag and NOT_READY. Reset confirmation sends no scanner command.

## Load help and export
`qt/app.py:load_command_text()` -> `messages.load_command(...)` -> `Bridge.loadCommand` -> `LoadHelpDialog.qml` (copy via `copyLoadCommand()`). `OPTICFILM_DRIVER_RUNNER`: unset/invalid selects distrobox `dev`; `host` runs directly; `distrobox:<name>` selects a container. The shown command wraps `cd -- <driver-dir> && PYTHONPATH=. python3 -m of135i load` in `bash -lc`, with the distrobox prefix when selected. `OPTICFILM_LOAD_COMMAND` overrides the displayed command; `loadRunnerNote` explains its runner.

`ExportDialog.qml` -> `Bridge.startExport(folder_url: str, fmt: str)` -> `Controller.export(directory: Path, fmt: Format)` -> `jobs.run_export(...)` -> immutable `ExportView` -> bridge properties/dialog.
Bridge flushes pending adjustments before jobs. Empty export explains already-exported, unaccepted and raw-freed counts via `exportMessage`. Re-export confirmation -> `Bridge.reexportAllAccepted()` -> `Controller.reexport_all_accepted()` -> `clear_exported(store)`; next export writes new numbered files, retaining old files. Removed raw scans can block re-export. Free-space confirmation rechecks eligibility and removes confirmed raw scans.

Only `app/qt/` imports PySide6; controller locks protect state, with jobs on worker pools.
