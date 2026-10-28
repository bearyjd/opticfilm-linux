"""Mirror and batch-wide orientation: model, manifest, option composition and pixels."""

import dataclasses
import json

import numpy as np
import pytest

from opticfilm_linux.batch.enhance import options_for
from opticfilm_linux.batch.manifest import ManifestFormatError, from_json, to_json
from opticfilm_linux.batch.model import (
    BatchDefaults,
    BatchManifest,
    SlideRecord,
    SlideSettings,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.model import Box, Slide
from opticfilm_linux.pipeline.orient import mirror, rotate
from opticfilm_linux.pipeline.run import ProcessOptions, enhance_slide


def test_slide_settings_and_defaults_have_no_orientation_by_default():
    assert SlideSettings().mirror is False
    defaults = BatchDefaults()
    assert (defaults.rotation, defaults.mirror) == (0, False)
    assert ProcessOptions().mirror is False


@pytest.mark.parametrize("rotation", [45, -90, 360, 1])
def test_batch_defaults_reject_other_rotations(rotation):
    with pytest.raises(ValueError, match="rotation"):
        BatchDefaults(rotation=rotation)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_batch_defaults_accept_quarter_turns(rotation):
    assert BatchDefaults(rotation=rotation, mirror=True).rotation == rotation


def _manifest() -> BatchManifest:
    slide = SlideRecord(position=1, settings=SlideSettings(rotation=90, mirror=True))
    tray = TrayRecord(number=1, stage=TrayStage.REVIEW, slides=(slide,))
    defaults = BatchDefaults(dpi=600, rotation=270, mirror=True)
    return BatchManifest(name="T", defaults=defaults, trays=(tray,))


def test_orientation_round_trips_through_the_manifest():
    assert from_json(to_json(_manifest())) == _manifest()


def test_a_manifest_written_before_orientation_existed_still_loads():
    data = json.loads(to_json(_manifest()))
    for key in ("rotation", "mirror"):
        del data["defaults"][key]
    del data["trays"][0]["slides"][0]["settings"]["mirror"]
    loaded = from_json(json.dumps(data))
    assert (loaded.defaults.rotation, loaded.defaults.mirror) == (0, False)
    assert loaded.trays[0].slides[0].settings == SlideSettings(rotation=90)


@pytest.mark.parametrize(
    "where, key, value",
    [
        ("defaults", "rotation", 45),
        ("defaults", "rotation", "90"),
        ("defaults", "rotation", False),
        ("defaults", "mirror", "false"),
        ("defaults", "mirror", 0),
        ("settings", "rotation", "90"),
        ("settings", "rotation", True),
        ("settings", "mirror", "false"),
        ("settings", "mirror", 1),
    ],
)
def test_a_bad_orientation_in_the_manifest_is_a_format_error(where, key, value):
    data = json.loads(to_json(_manifest()))
    target = data["defaults"] if where == "defaults" else data["trays"][0]["slides"][0]["settings"]
    target[key] = value
    with pytest.raises(ManifestFormatError):
        from_json(json.dumps(data))


@pytest.mark.parametrize("kind", [BatchDefaults, SlideSettings])
def test_mirror_must_be_a_real_bool(kind):
    with pytest.raises(ValueError, match="mirror"):
        kind(mirror="false")


ORIENTATIONS = [(r, m) for r in (0, 90, 180, 270) for m in (False, True)]


def _shown(image: np.ndarray, rotation: int, mirrored: bool) -> np.ndarray:
    """What the owner sees: turned clockwise, then flipped left-right."""
    turned = np.rot90(image, k=-(rotation // 90))
    return np.fliplr(turned) if mirrored else turned


@pytest.mark.parametrize("default", ORIENTATIONS)
@pytest.mark.parametrize("own", ORIENTATIONS)
def test_options_for_is_the_batch_orientation_followed_by_the_slides_own(default, own):
    image = np.arange(2 * 3, dtype=np.uint16).reshape(2, 3)  # no symmetry at all
    defaults = BatchDefaults(rotation=default[0], mirror=default[1])
    settings = SlideSettings(rotation=own[0], mirror=own[1])
    options = options_for(defaults, "positive", settings, 1)
    expected = _shown(_shown(image, *default), *own)
    assert np.array_equal(_shown(image, options.rotation, options.mirror), expected)


def test_mirror_flips_left_and_right_into_a_new_contiguous_array():
    image = np.arange(2 * 3 * 3, dtype=np.uint16).reshape(2, 3, 3)
    out = mirror(image)
    assert out is not image and out.flags["C_CONTIGUOUS"] and out.dtype == np.uint16
    assert np.array_equal(out[:, 0], image[:, 2]) and np.array_equal(out[:, 2], image[:, 0])
    assert np.array_equal(mirror(out), image)


def test_rotate_then_mirror_on_a_small_array_is_pixel_exact():
    pixels = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.uint16)
    image = np.repeat(pixels[:, :, None], 3, axis=2)
    out = mirror(rotate(image, 90))
    # turned clockwise: [[4,1],[5,2],[6,3]]; then flipped left-right
    assert np.array_equal(out[:, :, 0], np.array([[1, 4], [2, 5], [3, 6]], dtype=np.uint16))


def _slide() -> Slide:
    rng = np.random.default_rng(7)
    rgb = rng.integers(8000, 50000, size=(12, 18, 3), dtype=np.uint16)
    return Slide(index=0, rgb=rgb, ir=None, box=Box(0, 0, 18, 12))


def _plain() -> ProcessOptions:
    return ProcessOptions(remove_dust=False, grain_strength=0.0, sharpen_amount=0.0)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_enhance_slide_rotates_then_mirrors(rotation):
    base = enhance_slide(_slide(), _plain(), dpi=600)
    options = dataclasses.replace(_plain(), mirror=True, rotation=rotation)
    out = enhance_slide(_slide(), options, dpi=600)
    assert np.array_equal(out, rotate(base, rotation)[:, ::-1])


def test_enhance_slide_without_mirror_is_unchanged():
    options = dataclasses.replace(_plain(), rotation=90)
    out = enhance_slide(_slide(), options, dpi=600)
    assert np.array_equal(out, rotate(enhance_slide(_slide(), _plain(), dpi=600), 90))
