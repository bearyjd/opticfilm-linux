<!-- Generated: 2026-11-06 | Files scanned: 22 app + 12 app/qt + 28 qml = 62 | Token estimate: ~950 -->
# App layer (Qt-free controller + thin Qt shell)

Paths relative to `src/opticfilm_linux/app/`.

## Composition
Controller(FirstLoadCommands, HolderCommands, LifecycleCommands, ReviewCommands, ScanOpeningsCommands, SelectionCommands)
 controller.py 654  tick() -> bool, session (_Session: store, runner, poller, holder, scanner, auto_export), worker coordination
 state.py 634       frozen views; build_state(...) -> AppState (buttons, banners, trays, slides, out-of-date metadata keys)
 commands.py 577    ReviewCommands: slide edits, status, bulk edit, undo
 selection_commands.py 402 + selection.py 181: Selection/UndoEntry, click/extend/select_tray, metadata edits on explicit keys
 lifecycle_commands.py 183: new_batch / open_batch / close_batch (export, re-export, free space, Update JPEGs are in commands.py)
 holder.py 376      HolderCommands: eject_holder, load_next_holder, confirm_scanner_reset, holder latch sync, auto-eject
 first_load.py 145  guided first holder load; scan_openings.py 96: which openings the next scan covers
 status.py 136 StatusPoller (off-thread, take_eject_press TTL 2 s); jobs.py 250 run_export/run_free_space
 messages.py 220 UI text, load_command(driver_dir, runner); prefs.py 112; provenance.py/raw.py/recovery_checks.py/preview_tokens.py helpers

## Hands-free AutoPipeline (auto.py 487)
arm(n) / run(n): accept_tray(automatic) -> export_batch -> verify_tray_export -> guarded cleanup_raw.
resume() / retry_missing_destination() / retry_completed_exports(): restart after reopen. Holds on unreviewed flags,
empty frames, missing album folder; states pending -> exported -> verified. Runs on the controller's serial jobs worker.
Auto-eject: runner emits ScanFinished(number, ok, driver_exit); holder.py ejects once only if ok and exit code 0
(OPTICFILM_AUTO_EJECT); refusals say "Automatic eject skipped".

## Scanner ownership
Controller._attach(store, readonly) -> EjectWatch.yield_to_app() -> scanner factory -> poller/runner/holder.
eject_watch.py: EjectWatch(runner=run_systemctl, unit, process_check); yield_to_app() stops the unit, waits to settle,
checks for surviving `of135i watch` (watch_process_running via /proc); restore() restarts only if it was running.
sandbox.py: in_flatpak(), HOST_PREFIX=("flatpak-spawn","--host"), host_terminal_hint(). In Flatpak, systemctl runs on the
host; if the host cannot be asked the scanner is not opened (banner). Read-only batches open no scanner.
Eject press: DriverScanner.status_reading -> StatusPoller edge -> Controller.tick -> _act_on_eject_button -> eject_holder
(once, needs can_eject and fresh READY/NOT_READY; refuses while scans/export/free-space run).

## Qt shell (qt/, only PySide6 importer)
app.py 226 main(argv), scanner_factory(fake_sweep), load_command_text(), create_engine, window geometry
bridge.py 616 Bridge QObject: properties from AppState, slots -> Controller; start_bridge.py (new batch), selection.py,
sheet.py (contact-sheet rows), models.py, images.py, destination.py (debounced path checks off the Qt thread), settings.py
qml/ (28): Main, MainPage, StartPage, TrayPane/TrayQueue, SheetView, SlideThumb, InspectorPanel, ReviewDrawer,
MetadataStrip + TrayMetaDialog + EstimatedDateField (description/date), ScanOpeningsSelector, Export/FreeSpace/
LoadHelp/EmptyFrames/Orientation dialogs, FirstLoadCard, StatusArea, Theme.

## Flows
Export: ExportDialog -> Bridge.startExport(folder_url, fmt) -> Controller.export -> jobs.run_export -> ExportView.
Metadata: MetadataStrip -> Bridge slots -> setSlidesMeta(explicit keys) -> review.edit_meta_many; badge "not yet in JPEG"
 -> Update JPEGs -> meta_sync.update_exported_metadata.
Load help: messages.load_command (OPTICFILM_DRIVER_RUNNER; OPTICFILM_LOAD_COMMAND overrides; "host terminal" in Flatpak).
