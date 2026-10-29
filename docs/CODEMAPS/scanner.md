<!-- Generated: 2026-10-29 | Source: main @ 65a1a6f | Files: scanner/ 7 | Token estimate: ~450 -->
# Scanner layer

scanner/base.py 75        Scanner + HolderControl protocols; ScannerStatus enum; StatusReading(status, button)
scanner/driver.py 765     DriverScanner: real scanner via `python -m of135i` subprocess
  parse_doctor(text)        -> ScannerStatus (READY needs reg 0x01=0x22, holder present, status 0xdc..)
  parse_doctor_button(text) -> bool, True only for an `eject` line inside `== Button ==`
  status_reading()          -> StatusReading (button only when READY/NOT_READY)
  scan_holder(settings) -> RawSweep, eject(), load_next_strip()   (HolderControl protocol)
  one driver process at a time (process-wide _HardwareLock; ScannerInUse otherwise)
scanner/fake.py 53        FakeScanner: replays a stored sweep (tests, --fake)
scanner/interrupted.py 151 durable "a command may not have finished" mark -> ScannerInterrupted
scanner/select.py 39      pick backend; EJECT_BUTTON_ENV, eject_button_enabled()
scanner/usb.py 38         read-only sysfs detection of 07b3:1436 (`ofscan devices`)

Statuses: NOT_CONNECTED, BUSY, NOT_READY (connected, no tray loaded / not settled), READY, ERROR, UNKNOWN, INTERRUPTED.
BUTTON_STATUSES = (READY, NOT_READY): the only statuses where an Eject press may act.
Driver gates and hardware evidence live in the driver repo and .trail/learnings, not here.
