<!-- Generated: 2026-10-29 | Source: main @ 65a1a6f | Files scanned: 50 src + 3 scripts | Token estimate: ~600 -->
# Architecture

Native Linux scanning for the Plustek OpticFilm 135i: scan a 4-slide holder, get finished slides.
Single Python package `src/opticfilm_linux`, no server, no database (state = JSON manifest on disk).

## Entry points
ofscan   -> opticfilm_linux.cli:main            (batch/cli.py adds `ofscan batch ...`)
opticfilm-> opticfilm_linux.app.qt.app:main     (`--fake SWEEP_DIR` = fake scanner)
scripts/ check_spike.py, poll_report.py, rehearse.py, opticfilm-here

## Layers (imports point downward only)
 app.qt (QML/PySide6, only place importing Qt)
   -> app (Qt-free Controller, immutable AppState)
     -> batch (BatchStore manifest, BatchRunner, enhance, review, export)
       -> pipeline (split, dust, tone, detail, orient: numpy uint16 arrays)
       -> scanner (Scanner protocol: DriverScanner | FakeScanner)
            DriverScanner -> subprocess `python -m of135i` (community driver)
 model.py / output.py / sweepstore.py shared by scanner + pipeline

## Data flow (one tray)
scanner.scan_holder(ScanSettings) -> RawSweep -> split_slides -> per-slide enhance
 -> preview JPEGs + flags -> manifest (atomic JSON) -> review -> export (16-bit TIFF + sRGB)
Raw sweeps stay on disk (sweepstore .npy + meta.json); deleted only by the explicit export step.

## External
Driver: bearyjd/opticfilm135i-linux checkout via env OPTICFILM_DRIVER_DIR (or importable pkg)
Deps: numpy, pillow, tifffile, opencv-python-headless. PySide6 + Kirigami come from the distro.
Env: OPTICFILM_DRIVER_DIR, OPTICFILM_DRIVER_RUNNER, OPTICFILM_EJECT_BUTTON (0/false/no/off disables the button)
