# opticfilm-linux

Native Linux support for the **Plustek OpticFilm 135i** film scanner (USB `07b3:1436`): scan a
four-slide holder and get finished, enhanced slides.

> **Status: early development. Real scanning does not work yet.** Not affiliated with Plustek.

## What works today

- Splitting one whole-holder sweep into its four slides, and spotting empty windows.
- Enhancement per slide: infrared dust and scratch removal, exposure and colour-fade restoration,
  negative inversion, grain reduction and sharpening.
- Writing 16-bit TIFF (or 8-bit PNG/JPEG) with an sRGB profile and numbered, never-overwritten
  file names.
- Read-only detection of the scanner (`ofscan devices`).
- A fake scanner that replays stored sweeps, so everything above runs and is tested without
  hardware.

## What does not work yet

- **Scanning the real device.** The community driver
  ([opticfilm135i-linux](https://github.com/cgillinger/opticfilm135i-linux)) scans film strips but
  has no mounted-slide sweep yet, and this project builds on it rather than duplicating it.
  `ofscan scan` without `--fake` says so and exits with an error.
- Everything has only been tested on synthetic sweeps. Quality on real slides is unverified.
- Dust repair is done at 8-bit precision: repaired pixels lose 16-bit depth (the rest of the
  pipeline stays 16-bit).

## Quick start

```sh
uv sync
# make a synthetic 600 dpi four-slide sweep to play with
PYTHONPATH=tests uv run python -c "
from pathlib import Path
from synthetic import make_sweep
from opticfilm_linux.sweepstore import save_sweep
save_sweep(make_sweep(dpi=600, dust=12), Path('demo-sweep'))"
uv run ofscan process demo-sweep --out demo-out --album Demo
uv run ofscan devices
```

Commands: `ofscan devices`, `ofscan scan --fake SWEEP_DIR --dpi 600 --out DIR`,
`ofscan process SWEEP_DIR --out DIR` (options: `--album`, `--date`, `--format`, `--mode`, `--no-infrared`,
`--rotate {0,90,180,270}`, `--mirror`).
Exit code 2 and an `error:` line on bad input.

## Images from other scanning software

`ofscan enhance` runs the same enhancement and export on finished images from any other scanner
software (for example the vendor app in a Windows VM), one slide per image:

```sh
ofscan enhance scans/*.tif --out ~/Pictures/scans --album "Family Trips" --dpi 3600
```

TIFF, PNG and JPEG are read (8-bit is scaled to 16-bit); there is no infrared plane, so dust
removal is off. `--rotate` and `--mirror` work here too.

## Batch scanning

`ofscan batch` keeps a whole session in one folder, so a crash or a closed terminal loses at most
the tray being scanned:

```sh
ofscan batch new ~/slides/trips --name "Trips" --album "Family Trips" --dpi 600
ofscan batch scan ~/slides/trips --fake demo-sweep --count 3   # demo: replays a stored sweep
ofscan batch scan ~/slides/trips                               # real scanner: one tray; use --count 1
ofscan batch eject                       # between trays: eject the holder
ofscan batch load                        # load the next holder (needs OPTICFILM_NEXT_STRIP=1)
ofscan batch status ~/slides/trips
ofscan batch accept-all ~/slides/trips
ofscan batch export ~/slides/trips --out ~/Pictures/scans
```

- **On the real scanner, scan one tray per command.** `--count` above 1 scans the same holder
  again with no exchange in between; swap the holder with `eject` / `load`, then run `scan` again.
- **One program on the scanner at a time.** A lock file in `~/.local/state/opticfilm-linux/`
  keeps a second `ofscan` or the app out: `scan`, `eject` and `load` stop with "another program
  is using the scanner" instead of queueing, and the app's status shows busy.
- **A scan or holder move cut off mid-run** (Ctrl-C, a closed terminal, a killed program) leaves
  the scanner in an unknown state. The tray is flagged `interrupted: needs power-cycle + reload +
  rescan` and is never enhanced or exported, and every `scan`, `eject` and `load` (and the app's
  buttons) is refused until you power-cycle the scanner, load the holder again and run
  `ofscan batch confirm-scanner --yes` (in the app: **Confirm scanner reset…**). Then rescan the tray.
- **Enhancement runs in the background** while the next tray scans. Slides with no review flag are
  accepted automatically; flagged ones (very dark, very bright, clipped, dusty) wait for you.
  A choice you make on a slide is never overwritten later.
- **Raw scans are kept** in the batch folder (about 0.9 GB per tray at 3600 dpi, 3.5 GB at 7200; see [performance](docs/performance.md)), so settings can
  change without re-scanning. They are removed only by the explicit raw cleanup of an exported
  tray, never automatically. Export always recomputes slides from the raw scans.
- Scanning the real device still waits on the driver; the batch engine is tested against the
  fake scanner.

### Orientation

Mounted slides can come out turned or mirrored, and nobody knows yet which way the real scanner
delivers them. So orientation is set once per batch (`ofscan batch new --rotate 90 --mirror`, or
"Slide orientation" in the app) and fine-tuned per slide (rotate buttons, **Mirror**, keys `[`, `]`
and `m`). Every control acts on the image as you see it: a slide is turned first, then flipped
left-right, and the slide's own rotate and mirror apply on top of the batch's. Scan one tray, check
it, and change the whole batch later with **Batch orientation…**: every slide is enhanced again
and must be exported again; trays whose raw scans were removed keep their old orientation.

## Desktop app

`opticfilm` is one live window over the same batches: press **Scan** whenever the scanner says it
is ready, keep loading trays, and review the results as they arrive or all at the end.

- A queue of trays on the left (scanning, enhancing, ready to review, exported); the selected
  tray's four slides on the right with a before/after toggle, plain-words flags, and per-slide
  rotate, brightness, colour, grain and sharpening with "reset to auto".
- **Accept all**, **Export** (16-bit TIFF, 8-bit PNG or JPEG, with progress and cancel) and an
  explicit **Free disk space** dialog that says what it removes before it does.
- Nothing is lost on a crash or a closed window, and a second window on the same batch opens it
  read-only.

```sh
opticfilm --fake demo-sweep      # demo mode: runs on the fake scanner, no hardware needed
```

It needs the distribution's **PySide6 and Kirigami**, not the PyPI wheel (which bundles its own Qt
and cannot load the system Kirigami). On Fedora: `sudo dnf install python3-pyside6 kf6-kirigami`,
and create the environment with `uv venv --system-site-packages`. `ofscan` needs neither. For the
real scanner, see below.

### Using the app with the real scanner

The scanner has to be loaded by hand once after it is switched on; the app cannot do this step
(the driver asks you to press Enter). **How to load the scanner** in the toolbar walks through it:

1. Switch the scanner off, then on; wait for the steady light.
2. Slide the holder in loosely.
3. In a terminal, run the command the dialog shows (**Copy command**). By default it is
   `distrobox enter dev -- bash -lc 'cd <driver dir> && PYTHONPATH=. python3 -m of135i load'`,
   built from `OPTICFILM_DRIVER_DIR`; set `OPTICFILM_LOAD_COMMAND` to show a different one.
4. When it asks, pull the holder fully out, push it back in to the stop, and press Enter.
5. Check that it will not pull out (latched), then press **Scan**.

Between trays press **Eject holder**, swap the holder and push it in, then either repeat the steps
above or press **Load next holder**. Load next holder runs `of135i load --next-strip`, which is
not yet proven on this scanner, so it stays disabled unless the app is started with
`OPTICFILM_NEXT_STRIP=1`. When the driver refuses a scan the window says so in plain words:
power-cycle the scanner (off until its lights go out, then on), load the holder again and rescan
that tray. The driver's own message is under **Details**.

## Development

```sh
uv run pytest                       # default run, no hardware needed
uv run pytest -m hardware           # read-only check that the scanner is visible
uv run ruff check . && uv run ruff format --check .
```

See [CONTRIBUTING](docs/CONTRIBUTING.md) and [ATTRIBUTION](docs/ATTRIBUTION.md). Licence:
GPL-3.0-or-later.
