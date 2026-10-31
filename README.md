# opticfilm-linux

Native Linux support for the **Plustek OpticFilm 135i** film scanner (USB `07b3:1436`): scan a
four-slide holder and get finished, enhanced slides.

> **Status: early development. Real scanning has worked in limited tests on one unit; it is not
> yet reliable for a full tray at high resolution.** Not affiliated with Plustek. See
> [Real scanner status](#real-scanner-status).

## What works today

- Splitting one whole-holder sweep into its four slides, and spotting empty windows.
- Enhancement per slide: infrared dust and scratch removal, exposure and colour-fade restoration,
  negative inversion, grain reduction and sharpening.
- Writing 16-bit TIFF (or 8-bit PNG/JPEG) with an sRGB profile and numbered, never-overwritten
  file names.
- Read-only detection of the scanner (`ofscan devices`).
- A fake scanner that replays stored sweeps, so everything above runs and is tested without
  hardware.

## Real scanner status

Evidence below comes from supervised tests on a single scanner (one unit, one holder), using the
community driver ([opticfilm135i-linux](https://github.com/cgillinger/opticfilm135i-linux)) with
a mounted-slide path. It is not a general guarantee.

Verified natively on Linux:

- One slide (slide 4) at 600 dpi.
- All four slides at 600 dpi.
- One slide (slide 1) at 3600 dpi.
- One slide (slide 4) at 3600 dpi, with the driver's calibration stop gate on
  (`--cal-wait-scanstop enforce`): finished TIFFs, calibration integrity clean.
- All four slides in one tray scan at 3600 dpi (2026-10-31, one unit, one run, stop gate on):
  four finished frames, calibration integrity clean on each, about 5 minutes. The log shows two
  "poll timed out, continuing" warnings that the per-frame diagnostics do not count. One run on
  one scanner is not a reliability figure.
- The scanner's front-panel Eject button, through the driver's `watch` command: one press, one
  eject.

Not verified, or known problems:

- An earlier single-slide scan of slide 4 at 3600 dpi, without the stop gate, was interrupted
  after calibration warnings and produced no finished TIFF.
- The driver's experimental `--cal-wait-bufempty` flag is not usable yet: with the stop gate off
  it aborted the scan after 0.3 s, because the scanner's buffer-empty bit takes about 1 s to clear
  after the execute pulse. It stays off by default and is not needed with the stop gate on.
- A full tray at 7200 dpi is untested.
- Ejecting the holder from a cold start timed out twice during homing.
- The driver's startup recovery is experimental and opt-in.
- The calibration stop gate is on by default in the driver (`--cal-wait-scanstop off` disables
  it). In an interleaved A/B on 2026-10-30 (600 dpi, slide 4, one unit, order off/on/on/off/off/on
  after a warm-up), all three gate-off runs were degraded by a default dark-level fallback and all
  three gate-on runs were clean, so run order and warm-up do not explain the earlier result. That
  is three runs per arm on one scanner at one resolution. A gate-off run at 3600 dpi once came out
  clean, so the problem does not show up every time, and the gate has not been compared against
  gate-off runs at 3600 dpi. The measured image difference is a 1-2 count shadow offset, so image
  quality on real slides is not established. See
  [the combined review](docs/hardware-day-2026-10-28-review.md).
- The app's Eject-button handling (on by default; `OPTICFILM_EJECT_BUTTON=0` turns it off) was
  run on the real scanner by the owner on 2026-10-28 (one unit). No logs of that run are kept in
  this repository, and the cases that must do nothing (a press during a scan, a held press) are
  covered by fake-scanner tests only.

Do not treat the word "ejected" in any output as proof that the holder was released; check the
holder physically.

### Recovering after a failed scan

If a scan fails, is interrupted, or the scanner behaves oddly, stop sending commands and:

1. Power-cycle the scanner: switch it off and leave it off until its lights go out, then switch it
   on.
2. Load the holder again.
3. Rescan that tray from the start, only once the physical state is confirmed (see the checklist).

This is a cautious procedure from single-unit experience, not a validated repair. After an
interrupted or partly moved scan, the holder may still be retained: do not force it, and do not
send further commands until you know where it is.

See also the [hardware checklist](docs/hardware-checklist.md).

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
- Real-scanner use is limited to what is listed under [Real scanner status](#real-scanner-status);
  most of the batch engine is tested against the fake scanner.

### Orientation

Mounted slides can come out turned or mirrored, and nobody knows yet which way the real scanner
delivers them. So orientation is set once per batch (`ofscan batch new --rotate 90 --mirror`, or
"Slide orientation" in the app) and fine-tuned per slide (rotate buttons, **Mirror**, keys `[`, `]`
and `m`). Every control acts on the image as you see it: a slide is turned first, then flipped
left-right, and the slide's own rotate and mirror apply on top of the batch's. Scan one tray, check
it, and change the whole batch later with **Batch orientation…**: every slide is enhanced again
and must be exported again; trays whose raw scans were removed keep their old orientation.

If some holder slots are always loaded the same wrong way, give them a default when you create
the batch: `ofscan batch new ... --slot-rotate 2=180,3=180 --slot-mirror 1` (in the app: "Some
holder slots are always loaded differently"). It applies to that slot in every tray, on top of
the batch orientation, and is never stored as a choice on the slide: the slide's own rotate and
mirror still apply on top, and "reset to auto" goes back to the slot default. Slot defaults are
set when the batch is created and cannot be changed later. A batch with slot defaults cannot be
opened by versions released before this setting existed (they refuse it rather than ignore it).

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
- Optional: a systemd user service keeps the front-panel Eject button working with the app
  closed, and the app steps it aside while it uses the scanner; see
  [Eject button without the app](docs/eject-watch-service.md).

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

The scanner's front-panel Eject button ejects the holder, like Plustek's app, while the window is
idle (not while scanning or moving the holder); set `OPTICFILM_EJECT_BUTTON=0` to switch it off.

## Development

```sh
uv run pytest                       # default run, no hardware needed
uv run pytest -m hardware           # read-only check that the scanner is visible
uv run ruff check . && uv run ruff format --check .
```

See [CONTRIBUTING](docs/CONTRIBUTING.md) and [ATTRIBUTION](docs/ATTRIBUTION.md). Licence:
GPL-3.0-or-later.
