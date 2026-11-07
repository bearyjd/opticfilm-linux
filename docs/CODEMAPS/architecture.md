<!-- Generated: 2026-11-06 | Files scanned: 114 src + 12 scripts + 9 packaging | Token estimate: ~900 -->
# Architecture

Native Linux scanning for the Plustek OpticFilm 135i: scan a 4-slide holder, get finished slides.
Python >=3.11 package `src/opticfilm_linux`; no server, no database (state = JSON manifest on disk).

## Entry points (pyproject.toml [project.scripts])
ofscan    -> opticfilm_linux.cli:main        (`ofscan batch ...` -> batch/cli.py)
opticfilm -> opticfilm_linux.app.qt.app:main (GUI; `--fake SWEEP_DIR` = fake scanner)
scripts/: check_spike.py, poll_report.py (diagnostics), rehearse.py (profiling), opticfilm-here (launcher),
  install-eject-watch.sh, eject-watch-check.sh, hardware/ (supervised helpers, merge_when_green.sh)

## Layers (imports point downward only)
 app.qt (QML/PySide6, the only Qt importer)
   -> app (Qt-free Controller, immutable AppState, AutoPipeline)
     -> batch (BatchStore manifest, BatchRunner, enhance, review, export, meta_sync)
       -> pipeline (split, dust, tone, detail, orient: numpy uint16)
       -> scanner (Scanner protocol: DriverScanner | FakeScanner)
            DriverScanner -> subprocess `python -m of135i` (community driver)
 model.py output.py sweepstore.py provenance.py jpeg_rewrite.py: shared leaf modules

## Data flow (one tray)
scan_holder(ScanSettings) -> RawSweep -> split_slides -> enhance -> previews + flags -> manifest
 -> review/accept -> export (TIFF16 | PNG8 | JPEG, sRGB, XMP) -> verify -> optional raw cleanup
Hands-free (opt-in per batch: auto_export, default False): app/auto.py AutoPipeline accepts, exports, verifies, cleans.
OPTICFILM_AUTO_EJECT=1 (default off): after a good scan the app ejects the holder itself (holder.py).
Metadata (description/date) edits after export: batch/meta_sync.py + jpeg_rewrite.py patch XMP only.
Raw sweeps stay on disk until explicit/guarded cleanup after verified export.

## Eject watcher (opt-in systemd user service)
install-eject-watch.sh [--runner auto|host|distrobox[:N]] -> packaging/systemd/opticfilm-eject-watch{-host,}.service
 -> `of135i watch`; packaging/systemd/eject-watch-{run,stop}.sh wrap container runs; exit 75 = no restart.
app/eject_watch.py EjectWatch.yield_to_app() stops it before the app opens the scanner; restore() restarts it.

## Flatpak sandbox
packaging/flatpak/io.github.bearyjd.OpticFilmLinux.{json,desktop,metainfo.xml,svg}, runtime org.kde.Platform,
command `opticfilm`, --device=all, --talk-name=org.freedesktop.Flatpak, OPTICFILM_DRIVER_DIR=/app/share/...
app/sandbox.py in_flatpak() (only /.flatpak-info), HOST_PREFIX=(flatpak-spawn --host):
eject_watch.run_systemctl goes to host; unanswerable host query => scanner open refused;
messages.py says "host terminal" for load/systemctl hints.

## External
Driver: bearyjd/opticfilm135i-linux (pyusb inside it) via OPTICFILM_DRIVER_DIR or importable `of135i`.
Deps: numpy, pillow, tifffile, opencv-python-headless. PySide6 + Kirigami from the distro (not a pip dep).
Env: OPTICFILM_DRIVER_DIR, _DRIVER_RUNNER (host|distrobox:N, load-command text only), _LOAD_COMMAND,
 _EJECT_BUTTON, _NEXT_STRIP, _AUTO_EJECT, _SKIP_EMPTY, _SKIP_GAPS, _WINDOW_SIZE, _DRIVER_CLEAN.
See app.md, batch-pipeline.md, scanner.md.
