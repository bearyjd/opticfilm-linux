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
`ofscan process SWEEP_DIR --out DIR` (options: `--album`, `--date`, `--format`, `--mode`, `--no-infrared`).
Exit code 2 and an `error:` line on bad input.

## Images from other scanning software

`ofscan enhance` runs the same enhancement and export on finished images from any other scanner
software (for example the vendor app in a Windows VM), one slide per image:

```sh
ofscan enhance scans/*.tif --out ~/Pictures/scans --album "Family Trips" --dpi 3600
```

TIFF, PNG and JPEG are read (8-bit is scaled to 16-bit); there is no infrared plane, so dust
removal is off.

## Batch scanning

`ofscan batch` keeps a whole session in one folder, so a crash or a closed terminal loses at most
the tray being scanned:

```sh
ofscan batch new ~/slides/trips --name "Trips" --album "Family Trips" --dpi 600
ofscan batch scan ~/slides/trips --fake demo-sweep --count 3   # real scanning: not yet
ofscan batch status ~/slides/trips
ofscan batch accept-all ~/slides/trips
ofscan batch export ~/slides/trips --out ~/Pictures/scans
```

- **Enhancement runs in the background** while the next tray scans. Slides with no review flag are
  accepted automatically; flagged ones (very dark, very bright, clipped, dusty) wait for you.
  A choice you make on a slide is never overwritten later.
- **Raw scans are kept** in the batch folder (about 0.9 GB per tray at 3600 dpi, 3.5 GB at 7200; see [performance](docs/performance.md)), so settings can
  change without re-scanning. They are removed only by the explicit raw cleanup of an exported
  tray, never automatically. Export always recomputes slides from the raw scans.
- Scanning the real device still waits on the driver; the batch engine is tested against the
  fake scanner.

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
and create the environment with `uv venv --system-site-packages`. `ofscan` needs neither. Real
scanning still waits on the driver: with the real scanner the app says "status unknown" and keeps
Scan disabled.

## Development

```sh
uv run pytest                       # default run, no hardware needed
uv run pytest -m hardware           # read-only check that the scanner is visible
uv run ruff check . && uv run ruff format --check .
```

See [CONTRIBUTING](docs/CONTRIBUTING.md) and [ATTRIBUTION](docs/ATTRIBUTION.md). Licence:
GPL-3.0-or-later.
