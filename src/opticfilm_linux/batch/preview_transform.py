"""Which turn and flip carries a finished preview from one orientation to another.

Nothing here restates the orientation rules: the effective orientation comes from
``enhance.options_for`` and the transform is found by running the pipeline's own
``apply_orientation`` on a small asymmetric probe and trying the eight turn-and-flip candidates.
"""

import functools
import itertools

import numpy as np

from opticfilm_linux.batch.enhance import options_for
from opticfilm_linux.batch.model import ROTATIONS, BatchDefaults, Mode, SlideSettings
from opticfilm_linux.pipeline.orient import apply_orientation

Orientation = tuple[int, bool]
CANDIDATES: tuple[Orientation, ...] = tuple(itertools.product(ROTATIONS, (False, True)))
_PROBE = np.arange(6, dtype=np.uint16).reshape(2, 3, 1)  # no symmetry, so every candidate differs


def effective_orientation(
    defaults: BatchDefaults, mode: Mode, settings: SlideSettings, position: int
) -> Orientation:
    """How the pipeline turns and flips this slide (batch, then slot, then slide)."""
    options = options_for(defaults, mode, settings, position)
    return options.rotation, options.mirror


@functools.cache
def preview_transform(old: Orientation, new: Orientation) -> Orientation:
    """The one candidate that turns an image shown as ``old`` into that image shown as ``new``."""
    shown = apply_orientation(_PROBE, *old)
    wanted = apply_orientation(_PROBE, *new)
    matches = [
        candidate
        for candidate in CANDIDATES
        if np.array_equal(apply_orientation(shown, *candidate), wanted)
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one transform from {old} to {new}, found {matches}")
    return matches[0]


def transform_preview(image: np.ndarray, transform: Orientation) -> np.ndarray:
    """A new array: ``image`` turned and flipped by ``transform``."""
    return apply_orientation(image, *transform)
