<!-- Generated: 2026-11-06 | Files scanned: 9 scanner + select/usb | Token estimate: ~750 -->
# Scanner layer

Paths relative to src/opticfilm_linux/scanner/.
base.py          Scanner + HolderControl protocols; ScannerStatus enum; StatusReading(status, eject_pressed)
driver.py (979)  DriverScanner: real scanner, shells out to `python -m of135i <args>` (not an in-process import)
  DriverConfig.from_env(): OPTICFILM_DRIVER_DIR (checkout holding of135i/) or importable of135i; scratch/logs on disk
  OPTICFILM_DRIVER_RUNNER only changes the displayed load command (app/messages.py), not this backend
  parse_doctor(text) -> ScannerStatus (READY: reg 0x01=0x22, holder present, status 0xdc..)
  parse_doctor_button(text) -> bool (only an `eject` line inside `== Button ==`)
  status_reading() -> StatusReading: one `doctor` read, 3 s cache; button only if READY/NOT_READY
  scan_holder(settings: ScanSettings) -> RawSweep; eject() -> None; load_next_strip() -> None
  confirm_scanner_reset() -> bool: lock + driver-group check before clearing the interrupted mark
  _HardwareLock: process thread lock + cross-process file lock, else ScannerInUse
  First load after power-on must happen in a terminal (`of135i load`); TERMINAL_LOAD_HINT
holder_latch.py  HolderLatch enum; after_load/after_eject/invalidate; load_latch(bus, address), save_latch(...)
                 durable per-USB-device "holder is latched" memory (latch_state_path())
interrupted.py   durable mark "a command may not have finished" -> ScannerInterrupted
positions.py     slides_arg(positions) -> driver --slides argument (openings to scan)
fake.py          FakeScanner: replay a stored sweep + statuses (app --fake SWEEP_DIR)
select.py        real_scanner() -> DriverScanner; signal cleanup
  next_strip_enabled(): on unless OPTICFILM_NEXT_STRIP=0/false/no/off
  eject_button_enabled(): OPTICFILM_EJECT_BUTTON=0/false/no/off disables
  auto_eject_enabled(): OPTICFILM_AUTO_EJECT on (default off)
usb.py           find_devices(sysfs_root) -> list[DeviceInfo]: read-only sysfs detection of 07b3:1436 (`ofscan devices`)

App -> real_scanner -> DriverScanner -> of135i scan -> RGB/IR TIFFs -> stitched RawSweep
doctor -> status_reading -> app/status.py StatusPoller -> Controller.tick -> guarded holder command
app/eject_watch.py yields the systemd watcher before real-scanner access (see architecture.md).

Statuses: NOT_CONNECTED, BUSY, NOT_READY, READY, ERROR, UNKNOWN, INTERRUPTED.
BUTTON_STATUSES = (READY, NOT_READY): the only statuses where an Eject press may act.
Driver gates and hardware evidence live in the driver repo and .trail/learnings.
