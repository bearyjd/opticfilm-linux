<!-- Generated: 2026-11-01 | Source: main @ 39062e7 | Files scanned: 64 src + 11 scripts + 4 packaging/systemd | Token estimate: ~850 -->
# Architecture

Native Linux scanning for the Plustek OpticFilm 135i: scan a 4-slide holder, get finished slides.
Single Python package `src/opticfilm_linux`, no server, no database (state = JSON manifest on disk).

## Entry points
ofscan   -> opticfilm_linux.cli:main            (batch/cli.py adds `ofscan batch ...`)
opticfilm-> opticfilm_linux.app.qt.app:main     (`--fake SWEEP_DIR` = fake scanner)
scripts/ check_spike.py, poll_report.py (offline diagnostics), rehearse.py (profiling), opticfilm-here (Qt launcher)

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
Raw sweeps stay on disk (sweepstore .npy + meta.json); deleted only by explicit free-space cleanup after export.
Linear positive tone: shared gain -> highlight roll-off -> sRGB; export reports blocked/empty selections.
Re-export clears accepted-slide export marks atomically when raw exists -> review -> new numbered files.

## Opt-in Eject watcher (one systemd user service)
scripts/install-eject-watch.sh [--runner auto|host|distrobox[:NAME]] [DRIVER_DIR]
 -> packaging/systemd/opticfilm-eject-watch{-host,}.service -> of135i watch
Both templates install as opticfilm-eject-watch.service; auto probes host imports, else distrobox:dev.
Host: Python directly; container: eject-watch-run.sh / eject-watch-stop.sh wrappers in packaging/systemd/.
SIGINT-only stop, no SIGKILL; retry 30..120 s, exit 75 prevents restart after failed/interrupted eject.
app/eject_watch.py EjectWatch.yield_to_app() -> stop + settled unit + no watch process -> app scanner
Controller detach -> wait for holder/status idle -> EjectWatch.restore() (only if previously running).
scripts/eject-watch-check.sh: scanner-off startup/stop check; --live checks live stop; --enable is opt-in.
Installer renders/validates paths and refuses changed units while watcher runs; it does not start/enable.

## Supervised hardware helpers
scripts/hardware/hw_run.sh: driver -> trace/calibration/logs; failed run -> read-only doctor capture.
ab_gate_test.sh: balanced stop-gate A/B; tray_and_armc_test.sh: full tray or buffer-wait comparison.
These require terminal confirmation or supervised override; stop the watcher before terminal driver use.
merge_when_green.sh: optional maintainer CI/merge helper; README.md documents environment and supervision.

## External
Driver: bearyjd/opticfilm135i-linux checkout via env OPTICFILM_DRIVER_DIR (or importable pkg)
Deps: numpy, pillow, tifffile, opencv-python-headless. PySide6 + Kirigami come from the distro.
Env: OPTICFILM_DRIVER_DIR, OPTICFILM_DRIVER_RUNNER, OPTICFILM_EJECT_BUTTON (0/false/no/off disables the button)
OPTICFILM_DRIVER_RUNNER=host|distrobox:NAME selects the displayed terminal load command in app/messages.py (unset: distrobox:dev);
it does not change DriverScanner's subprocess backend. See app.md, batch-pipeline.md, scanner.md.
