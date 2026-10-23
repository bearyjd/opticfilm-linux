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


def test_a_bad_default_rotation_in_the_manifest_is_a_format_error():
    data = json.loads(to_json(_manifest()))
    data["defaults"]["rotation"] = 45
    with pytest.raises(ManifestFormatError):
        from_json(json.dumps(data))


@pytest.mark.parametrize(
    "default_rotation, slide_rotation, expected_rotation",
    [(0, 0, 0), (90, 0, 90), (0, 270, 270), (90, 90, 180), (270, 180, 90), (180, 180, 0)],
)
@pytest.mark.parametrize(
    "default_mirror, slide_mirror, expected_mirror",
    [(False, False, False), (True, False, True), (False, True, True), (True, True, False)],
)
def test_options_for_composes_batch_and_slide_orientation(
    default_rotation,
    slide_rotation,
    expected_rotation,
    default_mirror,
    slide_mirror,
    expected_mirror,
):
    defaults = BatchDefaults(rotation=default_rotation, mirror=default_mirror)
    settings = SlideSettings(rotation=slide_rotation, mirror=slide_mirror)
    options = options_for(defaults, "positive", settings)
    assert (options.rotation, options.mirror) == (expected_rotation, expected_mirror)


def test_mirror_flips_left_and_right_into_a_new_contiguous_array():
    image = np.arange(2 * 3 * 3, dtype=np.uint16).reshape(2, 3, 3)
    out = mirror(image)
    assert out is not image and out.flags["C_CONTIGUOUS"] and out.dtype == np.uint16
    assert np.array_equal(out[:, 0], image[:, 2]) and np.array_equal(out[:, 2], image[:, 0])
    assert np.array_equal(mirror(out), image)


def test_mirror_then_rotate_on_a_small_array_is_pixel_exact():
    pixels = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.uint16)
    image = np.repeat(pixels[:, :, None], 3, axis=2)
    out = rotate(mirror(image), 90)
    # mirrored: [[3,2,1],[6,5,4]]; then a clockwise quarter turn
    assert np.array_equal(out[:, :, 0], np.array([[6, 3], [5, 2], [4, 1]], dtype=np.uint16))


def _slide() -> Slide:
    rng = np.random.default_rng(7)
    rgb = rng.integers(8000, 50000, size=(12, 18, 3), dtype=np.uint16)
    return Slide(index=0, rgb=rgb, ir=None, box=Box(0, 0, 18, 12))


def _plain() -> ProcessOptions:
    return ProcessOptions(remove_dust=False, grain_strength=0.0, sharpen_amount=0.0)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_enhance_slide_mirrors_before_rotating(rotation):
    base = enhance_slide(_slide(), _plain(), dpi=600)
    options = dataclasses.replace(_plain(), mirror=True, rotation=rotation)
    out = enhance_slide(_slide(), options, dpi=600)
    assert np.array_equal(out, rotate(base[:, ::-1], rotation))


def test_enhance_slide_without_mirror_is_unchanged():
    options = dataclasses.replace(_plain(), rotation=90)
    out = enhance_slide(_slide(), options, dpi=600)
    assert np.array_equal(out, rotate(enhance_slide(_slide(), _plain(), dpi=600), 90))
