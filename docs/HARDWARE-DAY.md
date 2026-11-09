# Hardware day: first supervised trays

Owner at the scanner, one tray at a time. Details: [PRODUCTION.md](PRODUCTION.md). `<repo>` is the
main checkout. Defaults: 3600 dpi, auto-export on, skip-empty on, keep-raw on, auto-eject off,
Load & scan off. Leave auto-eject and Load & scan off until the last section.

## Before you start

- [ ] Look at the scanner: a holder from an earlier session (tray 2) may still be loaded. If so,
      eject it first (**Eject holder** in the app, or `of135i eject` in a real terminal), and
      confirm it is released before anything else.
- [ ] Update to main: `git -C <repo> checkout main && git -C <repo> pull`
- [ ] Close the app, stop the watcher, run preflight (read-only; never scans, loads or ejects):
```sh
systemctl --user stop opticfilm-eject-watch.service
python3 <repo>/scripts/hardware/preflight.py \
  --driver-dir <repo>/.local-research/publish/driver-latest --dest <batch-folder>
```
  Needs GO. Any FAIL: stop and read the line.
- [ ] Start the app (PRODUCTION.md section 1, unverified on this day's build):
```sh
distrobox-host-exec env PYTHONPATH=<repo>/src \
  OPTICFILM_DRIVER_DIR=<repo>/.local-research/publish/driver-latest \
  OPTICFILM_DRIVER_RUNNER=host \
  python3 -c "from opticfilm_linux.app.qt.app import main; raise SystemExit(main())"
```
- [ ] New batch outside the repo, on disk (not `/tmp`). Export folder set. dpi 3600.
- [ ] Guess box, for example `Maine trip, summer 1974, Kodachrome` then **Use suggestion**
      (or Ctrl+Enter). Check the date it fills; edit it if wrong.
- [ ] Keep raw scans after export: **on**.
- [ ] Holder loaded in the app (cold start: PRODUCTION.md 2.1).

## Tray 1

Keys: `S` scan, `E` eject, `L` load next, `X` open export, `F` finish box (confirm dialog).

1. Press **Scan** (`S`). Start a stopwatch.
2. Write down the time for: scan, enhance + export, eject, load next.
3. When it ends, check:
   - [ ] JPEGs exist in the export folder, one per slide, named `<NNN>.jpg`
         (heirloom layout: `box-<N>/<section>/<NNN>.jpg`).
   - [ ] Metadata, numbers only (do not open images):
         `exiftool -XMP-ofscan:all <file>.jpg` (unverified: tool and tag group name)
   - [ ] The Flagged list ("N flagged"): note the count. Dusty slides export anyway.
   - [ ] Raw scans are still on disk (keep-raw on). Nothing was freed early.
4. Eject (**Eject holder** or `E`), look, confirm released, swap the holder.

## Tray 2

1. **Load next holder** (`L`), then **Scan** (`S`). Time the same steps.
2. Repeat the Tray 1 checks.
3. If both trays passed: untick **Keep raw scans after export** for new work. This freeing
   path has never run on hardware, so watch the first tray after the change.

## Then

- [ ] Auto-eject: **Workflow settings…**, switch on **Automatic eject**, scan one tray.
      It should eject once the scan has fully ended. A banner "Automatic eject skipped" is
      the safe refusal. Look at the holder before pulling it. Switch it back off if unsure.
- [ ] Load & scan: **Workflow settings…**, switch on **Load & scan**. After an eject, swap the
      holder, press once (or Enter). Expect load, then scan.
- [ ] Close the app (it restores the watcher), or run
      `systemctl --user start opticfilm-eject-watch.service`.

## STOP conditions

Grinding noise, a stall, or an unexpected refusal banner: **power off, do not retry**, inspect
the holder before powering on. `of135i doctor` only if the scanner still responds. Load and
eject need a real terminal or the app, owner present. Never `pkill`, never force the holder.

## Numbers to write down

| Item | Seconds | Per slide |
|---|---|---|
| Scan (tray 1) | | |
| Scan (tray 2) | | |
| Enhance + export (tray 1) | | |
| Enhance + export (tray 2) | | |
| Eject | | |
| Load next | | |
| Auto-eject tray | | |
| Load & scan | | |
| Flagged count (tray 1 / tray 2) | | |

Expected from earlier single-unit measures: about 44 s scan per opening, about 73 s per slide
wall, 50 to 70 s enhance + export per tray.
