"""The dihedral transform that turns a preview from one effective orientation into another."""

import itertools

import numpy as np
import pytest

from opticfilm_linux.batch.model import ROTATIONS, BatchDefaults, SlideSettings, SlotOrientation
from opticfilm_linux.batch.preview_transform import (
    effective_orientation,
    preview_transform,
    transform_preview,
)
from opticfilm_linux.pipeline.orient import apply_orientation

ORIENTATIONS = tuple(itertools.product(ROTATIONS, (False, True)))
POSITION = 2


def _sample() -> np.ndarray:
    """A non-square image with no symmetry, different from the probe the module uses."""
    rng = np.random.default_rng(7)
    return rng.integers(0, 255, size=(5, 3, 3), dtype=np.uint8)


def _defaults(batch, slot) -> BatchDefaults:
    slots = () if slot == (0, False) else (SlotOrientation(POSITION, *slot),)
    return BatchDefaults(rotation=batch[0], mirror=batch[1], slots=slots)


def _check(old, new) -> None:
    image = _sample()
    shown = apply_orientation(image, *old)
    assert np.array_equal(
        transform_preview(shown, preview_transform(old, new)), apply_orientation(image, *new)
    )


@pytest.mark.parametrize("old", ORIENTATIONS)
@pytest.mark.parametrize("new", ORIENTATIONS)
def test_transform_maps_every_orientation_to_every_other(old, new):
    _check(old, new)


def test_same_orientation_is_the_identity():
    for orientation in ORIENTATIONS:
        assert preview_transform(orientation, orientation) == (0, False)


@pytest.mark.parametrize("batch", ORIENTATIONS)
@pytest.mark.parametrize("slot", ORIENTATIONS)
def test_slide_setting_changes_under_every_batch_and_slot_default(batch, slot):
    defaults = _defaults(batch, slot)
    for old, new in itertools.product(ORIENTATIONS, repeat=2):
        before = effective_orientation(
            defaults, "positive", SlideSettings(*old[:1], mirror=old[1]), POSITION
        )
        after = effective_orientation(
            defaults, "positive", SlideSettings(*new[:1], mirror=new[1]), POSITION
        )
        _check(before, after)


@pytest.mark.parametrize("slot", ORIENTATIONS)
@pytest.mark.parametrize("slide", ORIENTATIONS)
def test_batch_default_changes_under_every_slot_and_slide_setting(slot, slide):
    settings = SlideSettings(rotation=slide[0], mirror=slide[1])
    for old, new in itertools.product(ORIENTATIONS, repeat=2):
        before = effective_orientation(_defaults(old, slot), "negative", settings, POSITION)
        after = effective_orientation(_defaults(new, slot), "negative", settings, POSITION)
        _check(before, after)


def test_effective_orientation_follows_the_pipeline_options():
    defaults = _defaults((90, True), (180, False))
    settings = SlideSettings(rotation=270, mirror=True)
    from opticfilm_linux.batch.enhance import options_for

    options = options_for(defaults, "positive", settings, POSITION)
    assert effective_orientation(defaults, "positive", settings, POSITION) == (
        options.rotation,
        options.mirror,
    )


def test_transform_returns_a_new_array():
    image = _sample()
    out = transform_preview(image, (0, False))
    assert np.array_equal(out, image) and not np.shares_memory(out, image)


def test_quarter_turns_swap_the_preview_dimensions():
    image = _sample()
    assert transform_preview(image, preview_transform((0, False), (90, False))).shape == (3, 5, 3)
