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
- **load + split (tray)** (added after the first runs): `load_sweep` plus `split_slides` on the
  saved sweep, which is the fixed per-tray cost before any slide.

## Results

The machine has 22 cores and 62 GB of RAM, and other work was running on it (load average
9 to 23), so expect about ±30 % noise in the times. The scan stages time the fake driver, which
only generates frames. Real scan time is set by the scanner.

### After the fixes in this change

| dpi | tray total | scan + stitch | enhance 1 slide (batch work) | batch enhance (4) | export tiff16 (4) | peak anon | peak rss | raw sweep on disk | disk peak | 4 TIFFs |
|---|---|---|---|---|---|---|---|---|---|---|
| 600 | 3 s | 0.6 s | 0.2 s | 0.7 s | 0.6 s | 0.07 GB | 0.13 GB | 0.02 GB | 0.04 GB | 0.02 GB |
| 3600 | 61 s | 14 s | 2.5 s | 11 s | 9 s | 0.74 GB | 1.7 GB | 0.87 GB | 2.1 GB (+0.87 memmap) | 0.45 GB |
| 7200 | 195 s | 55 s | 7.6 s | 37 s | 30 s | 1.9 GB | 5.8 GB | 3.5 GB | 8.5 GB (+3.5 memmap) | 1.8 GB |

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
| Peak memory at 7200 dpi: 14 GB or less | 1.8 GB anon (5.4 GB RSS) | 1.9 GB anon (5.8 GB RSS) |
| A 3600 dpi tray (scan + enhance + export) takes minutes | 1.8 min | 1.0 min |
| A tray's enhancement takes at most about 4x one slide | 3600: 4.1x to 7.2x. 7200: 3.3x | 3600: 4.5x. 7200: 4.0x to 4.9x |

- **Memory** was never close to either budget. It went up by 0.1 to 0.25 GB because a few strips
  are now processed at once.
- **The 4x ratio is borderline, and these runs cannot settle it.** It compares the batch's four
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
  the radius squared. That is 16x per doubling of dpi.

Changes, all with byte-identical output:

1. `pipeline/arrays.map_strips` runs strips on a few threads, up to 8. It uses one worker per 8
   strips, so the float temporaries in flight stay below the size of the image. Small images
   (all 600 dpi slides) still run in the caller's thread. Each strip writes only its own rows.
2. `pipeline/dust.remove_dust` converts only each defect's patch to 8 bits, never the whole
   slide. That removed a full-slide uint16 temporary and a uint8 copy, and cut the function's
   peak memory from 2.7x the slide to below 1.8x. Patches are inpainted on a few threads and
   written back in contour order, so overlapping bounding boxes resolve exactly as they do on
   one thread.

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
