# Performance: what one tray costs

Measured with `scripts/rehearse.py`. It runs one four-slide tray through the real code path:
`DriverScanner.scan_holder`, `save_sweep`, a `BatchRunner` tray scan with background
enhancement, accept all, and `export_batch` to 16-bit TIFF. The fake `of135i` driver stands in
for the scanner (`FAKE_DRIVER_SYNTH=1`). It writes four synthetic full-window frames in the real
geometry at the requested dpi. A 600 dpi frame is 927 x 876 px (39.2 x 37 mm), with a dark holder
around a 38 x 25.5 mm opening, a gradient, noise, and six dust specks in the visible and IR planes.
No hardware is touched.

## Reproduce

```sh
uv run python scripts/rehearse.py --dpi 600
uv run python scripts/rehearse.py --dpi 3600
uv run python scripts/rehearse.py --dpi 7200 --work /some/disk/folder --keep
```

The default work folder is `~/.cache/opticfilm-linux/rehearsal`. Everything there is deleted
afterwards unless `--keep` is given. A 7200 dpi run needs about 9 GB of free disk while it runs.

## Columns

- **anon**: the peak anonymous memory of the app process (heap and numpy arrays), sampled every
  20 ms from `/proc/self/status` `RssAnon`. It excludes the page cache and the file-backed
  memmap pages of the sweep, which the kernel can drop at any time. The memory budgets are
  measured against this number.
- **rss**: the peak whole resident set, including those file-backed pages. `ru_maxrss` reports
  the same thing.
- **driver**: the peak RSS (`VmHWM`) of the driver subprocess, as the fake driver reports it.
  `RUSAGE_CHILDREN` is not used for this, because it includes pages the child inherits at fork.
- **disk peak**: the most the work folder held during the stage, sampled every 0.2 s. It
  includes the driver's frame TIFFs. It does not include the stitch's unlinked memmap, which
  needs rows x width x 8 bytes more until the sweep is saved: 0.87 GB at 3600 dpi, 3.5 GB at
  7200 dpi.
- **batch enhance (4)**: since the review fix, timed from the runner's own events, from the
  enhance job's `TrayStageChanged(ENHANCING)` to `TrayStageChanged(REVIEW)`. The job is queued
  just before the scan job finishes, so this no longer depends on when the scan stage's wait
  returns. (In practice the two differ by milliseconds.)
- **load + split (tray)** (added after the first runs): `load_sweep` plus `split_slides` on the
  saved sweep, which is the fixed per-tray cost before any slide.

## Results

The 2026-10-22 runs (further down) were made on the same 22-core, 62 GB machine, with other
work running (load average 9 to 23), so expect about ±30 % noise in the times. The scan stages time the fake driver, which
only generates frames. Real scan time is set by the scanner.

### Re-profile, 2026-10-29

Main at 5c4709d, after the dust 16-bit, tone, sharpen/grain-by-dpi and dust-edge changes.
Machine: Intel Core Ultra 9 185H, 22 logical cores, 62 GB RAM, work folder on a disk (not
RAM-backed). Other work was running (load average 4 to 15), so expect large noise. Runs
used `scripts/rehearse.py --dpi N`, no code change.

| dpi | script TOTAL (all stages) | scan + stitch | enhance 1 slide (batch work) | batch enhance (4) | export tiff16 (4) | peak anon | peak rss |
|---|---|---|---|---|---|---|---|
| 3600 | 41.5 s | 11.1 s | 2.2 s | 8.9 s | 6.7 s | 0.66 GB | 1.73 GB |
| 7200 | 194.5 s | 47.5 s | 12.3 s | 42.1 s | 31.7 s | 2.40 GB | 5.40 GB |

A second 7200 dpi run (busier machine) gave 233 s total, 10.3 s per slide, 68 s batch enhance,
38.8 s export, 2.40 GB anon, 5.16 GB rss: batch enhance swings by more than 50 % between runs.
"TOTAL" includes the standalone one-slide stages, as before; the stages a real tray pays
(batch scan + save, enhance, load + split, export) sum to 26 s at 3600 dpi and 122 s at 7200 dpi
in the first runs.

Peak anonymous memory per stage (anon / rss, GB):

| stage | 3600 | 7200 |
|---|---|---|
| driver scan + stitch | 0.24 / 1.07 | 0.90 / 3.13 |
| save_sweep | 0.03 / 0.93 | 0.02 / 3.44 |
| enhance_slide (1) | 0.48 / 1.72 | 1.66 / 5.40 |
| batch scan + save | 0.27 / 1.10 | 1.38 / 4.43 |
| batch enhance (4) | 0.50 / 1.21 | 1.95 / 4.56 |
| load + split (tray) | 0.20 / 0.46 | 0.70 / 1.62 |
| export tiff16 (4) | 0.66 / 1.37 | 2.40 / 5.04 |

Compared with 2026-10-22 (below): totals are unchanged within noise (3600: 37 to 41 s; 7200:
191 to 194 s). The export stage is now the memory peak (2.40 GB anon at 7200 dpi, up from 2.0
GB); the 7200 dpi RSS peak is 5.4 GB, under the 14 GB budget, and the whole 7200 dpi tray ran
with about 43 GB of RAM available. Per-slide enhancement at 7200 dpi looks slower (12.3 s
against 8.1 s), which is within the run-to-run swing seen here (10.3 to 13.7 s) but may also
reflect the 16-bit dust and tone changes; this was not isolated. The tray's enhancement is 4.0x
one slide at 3600 dpi and 3.4x at 7200 dpi in the first runs.

Hotspot inside one 7200 dpi slide (`enhance_slide`, two timed repetitions, 7.4 to 8.6 s each):

| step | seconds |
|---|---|
| sharpen | 3.3 to 3.7 |
| tone (exposure and colour) | 1.6 to 2.3 |
| remove_dust | 2.0 |
| reduce_grain | 0.5 to 0.7 |

`sharpen` is the largest step (about 40 to 45 % of the slide) but not dominant enough to justify
changing it without a bit-identical replacement; nothing was changed. Not profiled: export
(31.7 s, of which the defect mask's median blur is about 1.5 s) and the driver stitch (fake
driver, not real scan time). The real scan time is set by the scanner; these figures are only
the computer's share.

### After the fixes in this change (2026-10-22)

| dpi | tray total | scan + stitch | enhance 1 slide (batch work) | batch enhance (4) | export tiff16 (4) | peak anon | peak rss | raw sweep on disk | disk peak | 4 TIFFs |
|---|---|---|---|---|---|---|---|---|---|---|
| 600 | 3 s | 0.6 s | 0.2 s | 0.7 s | 0.6 s | 0.07 GB | 0.13 GB | 0.02 GB | 0.04 GB | 0.02 GB |
| 3600 | 37 s | 10 s | 1.9 s | 7.2 s | 5.7 s | 0.74 GB | 1.7 GB | 0.87 GB | 2.1 GB (+0.87 memmap) | 0.45 GB |
| 7200 | 191 s | 54 s | 8.1 s | 32.5 s | 31.6 s | 2.0 GB | 6.0 GB | 3.5 GB | 8.6 GB (+3.5 memmap) | 1.8 GB |

These rows are from the final code, with event-timed enhancement and the byte-bounded inpainting
from the review fixes (load average 12 to 15). Earlier after-runs on the first version of the
change, at load averages up to 23, gave 3600 dpi: 61 s total, 11 s batch enhance, 2.5 s per
slide; 7200 dpi: 195 to 215 s total, 37 to 38 s batch enhance, 7.6 to 9.5 s per slide.

### Before (main at 4cfcc05)

| dpi | tray total | enhance 1 slide (batch work) | batch enhance (4) | export tiff16 (4) | peak anon | peak rss |
|---|---|---|---|---|---|---|
| 600 | 6 s | 0.3 s | 1.5 s | 1.0 s | 0.07 GB | 0.12 GB |
| 3600 | 106 s | 4.4 s | 19 to 32 s | 19 to 25 s | 0.51 GB | 1.6 GB |
| 7200 | 371 s | 29 s | 96 s | 82 s | 1.8 GB | 5.4 GB |

The budgets:

| Budget | Before | After |
|---|---|---|
| Peak memory at 3600 dpi: 6 GB or less | 0.51 GB anon (1.6 GB RSS) | 0.74 GB anon (1.7 GB RSS) |
| Peak memory at 7200 dpi: 14 GB or less | 1.8 GB anon (5.4 GB RSS) | 2.0 GB anon (6.0 GB RSS) |
| A 3600 dpi tray (scan + enhance + export) takes minutes | 1.8 min | 0.6 to 1.0 min |
| A tray's enhancement takes at most about 4x one slide | 3600: 4.1x to 7.2x. 7200: 3.3x | final run: 3600 3.8x, 7200 4.0x. Earlier runs: 4.5x and 4.0x to 4.9x |

- **Memory** was never close to either budget. It went up by 0.1 to 0.25 GB because a few strips
  are now processed at once.
- **The 4x ratio is borderline.** The final event-timed run met it (3.8x and 4.0x), and the
  earlier runs on a busier machine did not (up to 4.9x). The event-based timing did not change
  the figure in a meaningful way. It compares the batch's four
  slides with one slide's batch work (enhance, both previews, flags) timed on its own. Fixed
  per-tray costs are small: mapping the sweep and `split_slides` take 0.2 s at 7200 dpi. The
  machine was fully loaded during the runs (load average up to 23 on 22 cores), so the
  ratio's precondition, "if cores are free", did not hold. Repeated runs swing by ±25 %.
  With per-slide work now about 4x faster, the same absolute noise shows up as a larger ratio.
  The absolute times are what matter on Saturday, and they halved.

## What was slow, and what changed

Before the fixes, one 7200 dpi slide spent most of its time in two places:

- **Single-threaded numpy**: the per-pixel float work in exposure, colour, grain and sharpening
  ran on one thread, although OpenCV threads its own filters.
- **Dust removal**: `cv2.inpaint` is single-threaded, and its cost grows with defect area times
  the radius squared. That was 16x per doubling of dpi while the mask dilation and the inpaint
  radius both scaled with dpi. Both are now fixed (2 px and 3 px at every dpi, since the infrared
  and colour planes register to 0 px), so the cost grows only with the defects' own area, and
  each channel is inpainted at 16 bits instead of 8.

Changes, all with byte-identical output:

1. `pipeline/arrays.map_strips` runs strips on a few threads, up to 8. It uses one worker per 8
   strips, so the float temporaries in flight stay below the size of the image. Small images
   (all 600 dpi slides) still run in the caller's thread. Each strip writes only its own rows.
2. `pipeline/dust.remove_dust` copies only each defect's patch, never the whole slide (at the
   time it converted patches to 8 bits; it now inpaints them at 16 bits, one channel at a time). That removed a full-slide uint16 temporary and a uint8 copy, and cut the function's
   peak memory from 2.7x the slide to below 1.8x. Patches are inpainted on a few threads and
   written back in contour order, so overlapping bounding boxes resolve exactly as they do on
   one thread. Patches are submitted lazily, and the ones in flight are bounded by bytes:
   their size stays at most min(256 MB, a quarter of the slide), and one patch always
   runs. Without that, a few long diagonal scratches, each with an almost full-image bounding
   box, would be inpainted all at once (the review measured 4x the old peak).
3. `map_strips` stays on the caller's thread when OpenCV's optimisations are switched off
   (`cv2.setUseOptimized(False)`). That switch is per thread, and worker threads would ignore
   it.

Tests run frozen copies of the pre-change implementations (`tests/reference_pipeline.py`)
beside the new code on every machine. The historical hashes are an extra check, enforced only
where a probe of the library kernels matches the development machine.

The batch runner's threading is unchanged: one scan worker and one enhance worker, with slides
enhanced in order and manifest writes in order. The parallelism is inside one slide's steps.

`tests/test_output_pinned.py` pins the 600 dpi outputs (enhanced pixels, both previews, flags)
by hash, and was written before any change. `tests/test_scaling.py` pins parallel-equals-serial
results, the write-back order, the worker bound, and the dust-removal memory bound.

## Not changed (fine at these sizes)

- `_stitch` reads one whole frame at a time with `tifffile.imread`, which is 0.7 GB at
  7200 dpi. It took 1.7 s at 3600 dpi.
- `save_sweep` copies the stitched memmap into the batch folder, so the sweep is briefly on disk
  twice.
- The defect mask's median blur (kernel 127 px at 3600 dpi, 253 px at 7200 dpi) runs three
  times per slide: dust removal, flags, and again at export. It takes about 1.5 s each time at
  7200 dpi.
- Previews and `split_slides` profiles take well under a second.
