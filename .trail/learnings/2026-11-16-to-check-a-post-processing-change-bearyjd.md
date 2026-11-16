---
title: "To check a post-processing change on real scans, compare old and new code on the same live data, never two scans"
author: Bearyjd
date: 2026-11-16
branch: docs/hardware-day-driver-by-hand
cost: { minutes: 3, tokens: 38335 }
id: l-47f6c81e
scope: repo
status: active
src: agent
touches:
  - docs/HARDWARE-DAY.md
---

## Decided
Two scans of the same slide never match byte for byte (sensor noise), so a "scan twice and compare SHA-256" check of a post-processing change can only fail for the wrong reason. Run ONE real scan and, at the moment the driver calls the image functions, also run the old code on the same inputs and compare outputs. Used 2026-11-15 for fork PR #30 (upstream #83 speedups, merged 76d2a85).
In the code: wrapper `~/slide-run/compare_scan.py` (not in the repo) patches `of135i.image.remove_dust`, `write_tiff16` and `align_channels`, loads the old module from `git show 497f42a:of135i/image.py`, then runs `cli.main(argv)` and prints SAME/DIFFERENT with SHA-256 prefixes. The driver calls these through the `image` module attribute, so patching works. Command: `scan --slide 1 --ir --dpi 3600 -o <file>` (IR on, so dust removal runs), in the dev container.

## Tried and ruled out
Two hardware scans, one per version, compared by SHA-256: noise alone makes them differ. Merging all of upstream master into the fork instead of the two #83 commits: conflicts in the core files.

## Watch out
Running old and new side by side doubles memory, so it cannot give a peak-memory figure for the new code; measure that with a separate normal run under `/usr/bin/time -v`. Host `python3` has no pyusb here: run driver commands in the dev container.
