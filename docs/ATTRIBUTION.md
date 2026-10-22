# Attribution

## opticfilm135i-linux

This project would not exist without
[cgillinger/opticfilm135i-linux](https://github.com/cgillinger/opticfilm135i-linux), an
unofficial community driver for the OpticFilm 135i (GPL-2.0-or-later or MIT for the driver,
CC BY 4.0 for its documentation). We use:

- the mounted-slide holder measurements from its documentation (apertures of about 22.75 mm along
  the sweep on a 62.6 mm pitch) as the geometry of our **synthetic test fixtures** in
  `tests/synthetic.py` and as the expected aperture length in `pipeline/split.py`. They were
  measured on one unit and must be re-checked on others;
- its documentation of the scanner's USB register interface as background for planning.

We plan to contribute to that project rather than fork it.

## No vendor material

This repository contains no Plustek code, binaries, firmware, ICC profiles, configuration
values, documentation or USB captures. Anything derived from vendor files stays out of the
repository (`.local-research/` is git-ignored). This project is not affiliated with or endorsed
by Plustek.
