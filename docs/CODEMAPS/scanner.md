<!-- Generated: 2026-11-01 | Source: main @ 39062e7 | Files: scanner/ 7 | Token estimate: ~600 -->
# Scanner layer

Paths below are relative to src/opticfilm_linux/; scanner/ has no changes since 65a1a6f.
scanner/base.py           Scanner + HolderControl protocols; ScannerStatus enum; StatusReading(status, eject_pressed)
scanner/driver.py         DriverScanner: real scanner via `python -m of135i` subprocess
  parse_doctor(text)        -> ScannerStatus (READY needs reg 0x01=0x22, holder present, status 0xdc..)
  parse_doctor_button(text) -> bool, True only for an `eject` line inside `== Button ==`
  status_reading() -> StatusReading: one doctor read, 3 s cache; button only when READY/NOT_READY
  scan_holder(settings: ScanSettings) -> RawSweep; eject() -> None; load_next_strip() -> None
  _HardwareLock: process-wide thread lock + cross-process file lock; ScannerInUse otherwise
  DriverConfig.from_env() -> DriverConfig: OPTICFILM_DRIVER_DIR or importable of135i; disk scratch/logs
scanner/fake.py           FakeScanner: load stored sweep, replay statuses; same scan/move interface
scanner/interrupted.py    durable "a command may not have finished" mark -> ScannerInterrupted
scanner/driver.py         confirm_scanner_reset() -> bool: lock + driver-group check before clearing mark
scanner/select.py        real_scanner() -> DriverScanner; installs signal cleanup
  next_strip_enabled() -> bool: OPTICFILM_NEXT_STRIP=1 gates hardware-unproven next-strip load
  eject_button_enabled() -> bool: OPTICFILM_EJECT_BUTTON=0/false/no/off disables front-panel handling
scanner/usb.py           find_devices(sysfs_root: Path = SYSFS_USB_DEVICES) -> list[DeviceInfo]
  read-only sysfs detection of 07b3:1436 (`ofscan devices`)

App -> real_scanner -> DriverScanner -> of135i scan -> RGB/IR TIFFs -> stitched RawSweep
doctor -> status_reading -> app/status.py StatusPoller -> Controller.tick -> guarded holder command
app/eject_watch.py yields the separate systemd watcher before real-scanner access (see architecture.md).
OPTICFILM_DRIVER_RUNNER affects shown load-command text in app/messages.py; scanner execution is local Python.

Statuses: NOT_CONNECTED, BUSY, NOT_READY (connected, no tray loaded / not settled), READY, ERROR, UNKNOWN, INTERRUPTED.
BUTTON_STATUSES = (READY, NOT_READY): the only statuses where an Eject press may act.
Driver gates and hardware evidence live in the driver repo and .trail/learnings, not here.
