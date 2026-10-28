"""Per-slot default orientation: the model, its composition and the manifest."""

import json

import numpy as np
import pytest

from opticfilm_linux.batch.enhance import options_for
from opticfilm_linux.batch.manifest import ManifestFormatError, from_json, to_json
from opticfilm_linux.batch.model import (
    BatchDefaults,
    BatchManifest,
    SlideSettings,
    SlotOrientation,
)
from opticfilm_linux.batch.slots import compose, slot_orientations

ORIENTATIONS = [(r, m) for r in (0, 90, 180, 270) for m in (False, True)]


def _shown(image: np.ndarray, rotation: int, mirrored: bool) -> np.ndarray:
    """What the owner sees: turned clockwise, then flipped left-right."""
    turned = np.rot90(image, k=-(rotation // 90))
    return np.fliplr(turned) if mirrored else turned


# -- the model -----------------------------------------------------------------------------


def test_batch_defaults_have_no_slot_orientations_by_default():
    assert BatchDefaults().slots == ()


@pytest.mark.parametrize("position", [0, -1, True, 1.0, "2"])
def test_a_slot_position_must_be_a_positive_whole_number(position):
    with pytest.raises(ValueError, match="slot"):
        SlotOrientation(position=position, rotation=90)


@pytest.mark.parametrize("rotation, mirror", [(45, False), ("90", False), (90, "true")])
def test_a_slot_orientation_must_be_valid(rotation, mirror):
    with pytest.raises(ValueError):
        SlotOrientation(position=1, rotation=rotation, mirror=mirror)


def test_a_slot_that_changes_nothing_is_refused():
    with pytest.raises(ValueError, match="slot 2"):
        SlotOrientation(position=2)


def test_slots_must_be_in_order_and_each_slot_only_once():
    one, two = SlotOrientation(1, 90), SlotOrientation(2, 180)
    assert BatchDefaults(slots=(one, two)).slots == (one, two)
    with pytest.raises(ValueError, match="order"):
        BatchDefaults(slots=(two, one))
    with pytest.raises(ValueError, match="order"):
        BatchDefaults(slots=(one, SlotOrientation(1, 180)))


def test_slots_must_be_a_tuple_of_slot_orientations():
    with pytest.raises(ValueError, match="slots"):
        BatchDefaults(slots=[SlotOrientation(1, 90)])
    with pytest.raises(ValueError, match="slots"):
        BatchDefaults(slots=((1, 90, False),))


def test_slot_orientations_builds_the_canonical_tuple():
    slots = slot_orientations({3: 180, 2: 90, 4: 0}, mirrored=(4, 1))
    assert slots == (
        SlotOrientation(1, 0, True),
        SlotOrientation(2, 90),
        SlotOrientation(3, 180),
        SlotOrientation(4, 0, True),
    )
    assert slot_orientations({1: 0}, ()) == ()  # a slot left as it is is not stored


def test_slot_orientations_rejects_a_bad_rotation():
    with pytest.raises(ValueError, match="rotation"):
        slot_orientations({2: 45}, ())


# -- composition ---------------------------------------------------------------------------


@pytest.mark.parametrize("first", ORIENTATIONS)
@pytest.mark.parametrize("then", ORIENTATIONS)
def test_compose_is_the_first_orientation_followed_by_the_second(first, then):
    image = np.arange(2 * 3, dtype=np.uint16).reshape(2, 3)  # no symmetry at all
    expected = _shown(_shown(image, *first), *then)
    assert np.array_equal(_shown(image, *compose(first, then)), expected)


@pytest.mark.parametrize("batch", ORIENTATIONS)
@pytest.mark.parametrize("slot", ORIENTATIONS)
@pytest.mark.parametrize("own", ORIENTATIONS)
def test_options_for_turns_by_batch_then_slot_then_the_slides_own(batch, slot, own):
    image = np.arange(2 * 3, dtype=np.uint16).reshape(2, 3)
    slots = slot_orientations({2: slot[0]}, (2,) if slot[1] else ())
    defaults = BatchDefaults(rotation=batch[0], mirror=batch[1], slots=slots)
    settings = SlideSettings(rotation=own[0], mirror=own[1])
    options = options_for(defaults, "positive", settings, 2)
    expected = _shown(_shown(_shown(image, *batch), *slot), *own)
    assert np.array_equal(_shown(image, options.rotation, options.mirror), expected)


def test_options_for_other_slots_ignore_a_slot_default():
    defaults = BatchDefaults(rotation=90, slots=(SlotOrientation(2, 180, True),))
    options = options_for(defaults, "positive", SlideSettings(), 1)
    assert (options.rotation, options.mirror) == (90, False)


# -- the manifest --------------------------------------------------------------------------


def _manifest() -> BatchManifest:
    slots = (SlotOrientation(1, 270), SlotOrientation(3, 180, True))
    return BatchManifest(name="T", defaults=BatchDefaults(dpi=600, slots=slots))


def test_slot_orientations_round_trip_through_the_manifest():
    assert from_json(to_json(_manifest())) == _manifest()


def test_a_manifest_written_before_slot_orientations_existed_still_loads():
    data = json.loads(to_json(_manifest()))
    del data["defaults"]["slots"]
    assert from_json(json.dumps(data)).defaults == BatchDefaults(dpi=600)


@pytest.mark.parametrize(
    "slots",
    [
        "2=180",
        {},
        [{"position": 1, "rotation": 45, "mirror": False}],
        [{"position": 0, "rotation": 90, "mirror": False}],
        [{"position": 1, "rotation": 90}],
        [{"position": 1, "rotation": 90, "mirror": False, "extra": 1}],
        [{"position": 2, "rotation": 90, "mirror": False}] * 2,
        [7],
    ],
)
def test_a_bad_slot_orientation_in_the_manifest_is_a_format_error(slots):
    data = json.loads(to_json(_manifest()))
    data["defaults"]["slots"] = slots
    with pytest.raises(ManifestFormatError):
        from_json(json.dumps(data))


def test_a_batch_without_slot_orientations_is_written_as_before():
    """Older versions refuse unknown keys, so only a batch that uses slots carries the key."""
    data = json.loads(to_json(BatchManifest(name="T", defaults=BatchDefaults(rotation=90))))
    assert "slots" not in data["defaults"]
    assert "slots" in json.loads(to_json(_manifest()))["defaults"]
