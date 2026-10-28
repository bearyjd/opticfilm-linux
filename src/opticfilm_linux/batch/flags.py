"""Reasons a finished slide deserves a second look. Thresholds are tuning guesses."""

import numpy as np

from opticfilm_linux.batch.model import Flag
from opticfilm_linux.pipeline.dust import defect_mask_for

FULL_SCALE = 65535
DARK_MEAN = 0.12  # mean brightness below this: too dark
BRIGHT_MEAN = 0.88  # mean brightness above this: too bright
CLIP_FRACTION = 0.02  # more than this share of values pure black or white: clipping
DUST_FRACTION = 0.005  # more than this share of the image masked as defects: dusty
SAMPLE_STEP = 4


def compute_flags(
    enhanced: np.ndarray, ir: np.ndarray | None, dpi: int, visible: np.ndarray | None = None
) -> tuple[Flag, ...]:
    """``visible`` is the slide's raw colour image, registered to ``ir`` (``enhanced`` may be
    turned or mirrored): with it, infrared dips on its strong edges do not count as dust."""
    sample = enhanced[::SAMPLE_STEP, ::SAMPLE_STEP]
    mean = float(sample.mean()) / FULL_SCALE
    clipped = float(np.mean((sample == 0) | (sample == FULL_SCALE)))
    flags = []
    if mean < DARK_MEAN:
        flags.append(Flag.TOO_DARK)
    if mean > BRIGHT_MEAN:
        flags.append(Flag.TOO_BRIGHT)
    if clipped > CLIP_FRACTION:
        flags.append(Flag.CLIPPING)
    if ir is not None and float(defect_mask_for(ir, dpi, visible=visible).mean()) > DUST_FRACTION:
        flags.append(Flag.DUST)
    return tuple(flags)
