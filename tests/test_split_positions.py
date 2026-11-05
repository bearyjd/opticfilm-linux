"""Openings of a sweep map back to their holder positions, whichever positions were scanned."""

import dataclasses

import numpy as np
import pytest

from opticfilm_linux.model import ALL_POSITIONS, HolderKind, RawSweep, ScanSettings
from opticfilm_linux.pipeline.split import SlideSplitError, split_positions, split_slides
from synthetic import make_sweep


def _partial(positions: tuple[int, ...]) -> RawSweep:
    """A sweep holding only ``len(positions)`` openings, as stitched from those frames."""
    sweep = make_sweep(dpi=600, filled=(True,) * len(positions))
    return dataclasses.replace(sweep, positions=positions)


def test_a_full_sweep_maps_its_openings_to_positions_one_to_four():
    sweep = make_sweep(dpi=600)
    slides = split_positions(sweep)
    assert tuple(slides) == ALL_POSITIONS == sweep.positions
    for opening, slide in zip(split_slides(sweep), slides.values(), strict=True):
        assert np.array_equal(slide.rgb, opening.rgb)


@pytest.mark.parametrize("positions", [(1, 3), (2, 4), (1, 2, 3), (4,)])
def test_a_partial_sweep_maps_each_opening_to_its_scanned_position(positions):
    sweep = _partial(positions)
    slides = split_positions(sweep)
    openings = split_slides(sweep, expected=len(positions))
    assert tuple(slides) == positions
    for position, opening in zip(positions, openings, strict=True):
        assert np.array_equal(slides[position].rgb, opening.rgb)


def test_a_sweep_whose_openings_do_not_match_its_positions_is_refused():
    sweep = dataclasses.replace(make_sweep(dpi=600), positions=(1, 3))
    with pytest.raises(SlideSplitError, match="expected 2"):
        split_positions(sweep)


@pytest.mark.parametrize(
    "positions", [(), (3, 1), (1, 1), (0, 1), (1, 5), (1.0, 2), (True, 2), [1, 2]]
)
def test_positions_must_be_ascending_unique_holder_openings(positions):
    sweep = make_sweep(dpi=600)
    with pytest.raises(ValueError, match="positions"):
        RawSweep(sweep.rgb, sweep.ir, 600, HolderKind.SLIDE4, positions=positions)
    with pytest.raises(ValueError, match="positions"):
        ScanSettings(positions=positions)


def test_the_default_is_all_four_openings():
    assert ScanSettings().positions == ALL_POSITIONS == (1, 2, 3, 4)
