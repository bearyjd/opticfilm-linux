<!-- Generated: 2026-10-29 | Source: main @ 65a1a6f | Files: app/ 8 + app/qt/ 6 | Token estimate: ~650 -->
# App layer (Qt-free controller + thin Qt shell)

## Files (lines)
app/controller.py 603  Controller: the window's brain; tick() drives polling + Eject button
app/state.py 195       build_state(...) -> frozen AppState (can_scan, can_eject, banners, trays)
app/status.py 111      StatusPoller: runs scanner status off-thread; take_eject_press(); PRESS_TTL_S=2.0
app/holder.py 160      HolderCommands (Controller base): eject_holder, load_next_holder, confirm_scanner_reset
app/jobs.py 82         export / free-space jobs reporting immutable progress
app/messages.py 108    plain-words UI text (pure functions)
app/prefs.py 67        Prefs interface + recent batches
app/qt/app.py 130      main(): start window (--fake)
app/qt/bridge.py 424   QObject mirroring AppState to QML; slots -> Controller
app/qt/{images,models,settings}.py  image://preview provider, dict list model, QSettings Prefs

## Front-panel Eject button (PR #32)
DriverScanner.status_reading() -> StatusReading(status, button)   [one `doctor` per poll, 3 s cache]
 -> StatusPoller._ask  (baseline on first/gap/non-READY reading; rising edge only)
 -> Controller.tick -> _act_on_eject_button(poller)
      requires: eject_button_enabled(), state.can_eject, poller.current() in {READY, NOT_READY}
      -> HolderCommands.eject_holder -> _move_holder (existing guards, banner on failure, no retry)
Dropped, never queued: presses while busy / scanning / read-only; stale presses (>2 s).

## Rules
Only app/qt imports PySide6. State is immutable snapshots; Controller holds the lock, jobs run on pools.
