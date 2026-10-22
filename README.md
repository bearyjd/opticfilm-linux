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
`ofscan process SWEEP_DIR --out DIR` (options: `--album`, `--date`, `--format`, `--mode`).
Exit code 2 and an `error:` line on bad input.

## Development

```sh
uv run pytest                       # default run, no hardware needed
uv run pytest -m hardware           # read-only check that the scanner is visible
uv run ruff check . && uv run ruff format --check .
```

See [CONTRIBUTING](docs/CONTRIBUTING.md) and [ATTRIBUTION](docs/ATTRIBUTION.md). Licence:
GPL-3.0-or-later.
