"""Linear scanner positives: per-channel black level first, then one shared curve.

Raw scans are linear with the scene median at a few percent of full range and a slightly
different black level per channel. A shared gamma applied before the per-channel black is
removed bends each channel's offset differently, which no later linear stretch can undo (a
colour cast), and the old gamma floor of 0.5 left such scans dark. Fixtures are synthetic.
"""

import warnings

import numpy as np
import pytest

from opticfilm_linux.model import Box, Slide
from opticfilm_linux.pipeline.run import ProcessOptions, enhance_slide
from opticfilm_linux.pipeline.tone import (
    MAX_BLACK_OFFSET,
    auto_exposure,
    invert_negative,
    restore_color,
    subtract_black,
)

FULL = 65535
GREEN_BLACK = 0.011  # green's extra black level, as measured on the real scanner
GREEN_CLIP = 0.6  # green saturates below the other channels
H, W = 240, 360
MOUNT = 36  # columns of opaque mount on each side, as an uncropped slide has
# Opaque mount reads the sensor's dark level, which differs a little per channel and lies below
# the film's own darkest density (here a base of 0.01).
MOUNT_LEVEL = (0.003, 0.013, 0.0)
FILM_BASE = 0.01
PLAIN = ProcessOptions(remove_dust=False, sharpen_amount=0.0, grain_strength=0.0)


def _dark_linear_scene(mount: bool = False) -> np.ndarray:
    """A neutral, linear, dark scene (median 0.04) with green offset and clipped at the top."""
    t = (np.arange(H * W, dtype=np.float64) + 0.5) / (H * W)
    scene = (0.64 * t**4).reshape(H, W)  # median 0.04, a few highlights up to 0.64
    scene = np.random.default_rng(3).permuted(scene.ravel()).reshape(H, W)
    if mount:
        scene = scene + FILM_BASE
    rgb = np.stack([scene, np.minimum(scene + GREEN_BLACK, GREEN_CLIP), scene], axis=2)
    if mount:
        rgb[:, :MOUNT] = MOUNT_LEVEL
        rgb[:, -MOUNT:] = MOUNT_LEVEL
    return np.rint(rgb * FULL).astype(np.uint16)


def _slide(rgb: np.ndarray) -> Slide:
    return Slide(index=0, rgb=rgb, ir=None, box=Box(0, 0, rgb.shape[1], rgb.shape[0]))


def _interior(image: np.ndarray) -> np.ndarray:
    return image[:, MOUNT:-MOUNT].reshape(-1, 3).astype(np.float64) / FULL


def _balance(image: np.ndarray) -> float:
    means = _interior(image).mean(axis=0)
    return float(means.max() / means.min())


def _median(image: np.ndarray) -> float:
    return float(np.median(_interior(image).mean(axis=1)))


def test_a_dark_linear_scan_comes_out_exposed_and_neutral():
    out = enhance_slide(_slide(_dark_linear_scene()), PLAIN, 600)
    assert abs(_median(out) - 0.45) <= 0.05
    assert _balance(out) < 1.03


def test_a_dark_scan_with_its_mount_still_comes_out_neutral_and_brighter():
    """Exposure statistics are whole-frame, so mount pixels lift the slide a little; cropping the
    mount is the splitter's job. The colour must not be fooled by it."""
    out = enhance_slide(_slide(_dark_linear_scene(mount=True)), PLAIN, 600)
    assert 0.4 <= _median(out) <= 0.65
    assert _balance(out) < 1.04


def test_per_channel_stretch_after_the_curve_would_cast_the_mount_scan():
    """The display curve magnifies a cast when per-channel stretching comes afterward."""
    image = _dark_linear_scene(mount=True)
    curved_first = restore_color(auto_exposure(subtract_black(image), linear=True))
    assert _balance(curved_first) > 1.08


def test_the_old_order_was_dark_and_green_deficient():
    """The yardstick the new path is measured against: shared gamma first, floor 0.5."""
    old = restore_color(auto_exposure(_dark_linear_scene()))
    assert _median(old) < 0.35
    means = _interior(old).mean(axis=0)
    assert means[1] < 0.97 * means[0]


def test_the_black_level_comes_from_the_interior_not_the_mount():
    """Mount darker than the film still carries the scanner's offset; the film's level is used."""
    image = _dark_linear_scene(mount=True)
    lows = np.percentile(_interior(subtract_black(image)), 0.5, axis=0)
    assert abs(lows[1] - lows[0]) < 0.001  # green's offset was removed, not just the mount's


def _neutral_edges_coloured_middle() -> np.ndarray:
    """Neutral dark shadows around the frame, a coloured subject in the middle (Codex probe)."""
    x = np.linspace(0.01, 0.02, 10_000).reshape(100, 100)
    rgb = np.stack([x] * 3, axis=2)
    rgb[20:80, 20:80] += [0.2, 0.1, 0.0]
    return np.rint(rgb * FULL).astype(np.uint16)


def test_a_coloured_middle_does_not_push_neutral_edge_shadows_below_zero():
    image = _neutral_edges_coloured_middle()
    assert np.array_equal(subtract_black(image), image)


@pytest.mark.parametrize("strength", [0.0, 1.0])
def test_a_coloured_middle_leaves_the_edges_neutral_through_the_pipeline(strength):
    options = ProcessOptions(
        remove_dust=False, sharpen_amount=0.0, grain_strength=0.0, color_strength=strength
    )
    out = enhance_slide(_slide(_neutral_edges_coloured_middle()), options, 600)
    assert np.mean(out == 0, axis=(0, 1)).max() < 0.01
    if strength == 0.0:
        edge = out[:10].reshape(-1, 3).astype(np.float64).mean(axis=0)
        assert edge.max() / edge.min() < 1.01


def test_subtract_black_removes_each_channels_own_offset():
    out = subtract_black(_dark_linear_scene())
    lows = np.percentile(_interior(out), 0.5, axis=0)
    assert np.all(lows < 0.001)


def test_subtract_black_never_wraps_below_zero():
    image = _dark_linear_scene()
    image[0, 0] = 0  # darker than the estimated black level
    out = subtract_black(image)
    assert out.dtype == np.uint16 and out[0, 0].tolist() == [0, 0, 0]


def test_a_channel_with_no_range_keeps_its_level():
    image = _dark_linear_scene()
    image[..., 1] = FULL  # a fully clipped green has no black level to find
    out = subtract_black(image)
    assert np.array_equal(out[..., 1], image[..., 1])


@pytest.mark.parametrize("level", [0.0, 0.3, 1.0])
def test_uniform_frames_are_left_alone(level):
    flat = np.full((32, 32, 3), np.uint16(level * FULL), np.uint16)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert np.array_equal(subtract_black(flat), flat)
        assert np.array_equal(enhance_slide(_slide(flat), PLAIN, 600), flat)


def test_a_noisy_blank_frame_stays_finite_and_in_range():
    rng = np.random.default_rng(0)
    noisy = np.clip(rng.normal(0.3, 0.002, (64, 64, 3)) * FULL, 0, FULL).astype(np.uint16)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        out = enhance_slide(_slide(noisy), PLAIN, 600)
    assert out.dtype == np.uint16 and out.shape == noisy.shape


def test_tiny_frames_do_not_fail():
    tiny = np.arange(12, dtype=np.uint16).reshape(2, 2, 3) * 1000
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert subtract_black(tiny).shape == tiny.shape


def test_the_linear_gain_cap_keeps_a_near_black_frame_dark():
    """A frame that is black but for a few bright specks must not have its noise lifted to grey:
    the gain cap keeps its median well below the target."""
    rng = np.random.default_rng(1)
    dark = rng.uniform(0.001, 0.003, (100, 100, 3))
    dark[:2, :] = 1.0  # 2 % bright pixels set the white point
    image = np.rint(dark * FULL).astype(np.uint16)
    out = auto_exposure(image, linear=True)
    assert float(np.median(out)) / FULL < 0.35


def _neutral_ramp() -> np.ndarray:
    ramp = np.linspace(0.02, 0.98, W)[None, :].repeat(H, axis=0)
    return np.rint(np.stack([ramp] * 3, axis=2) * FULL).astype(np.uint16)


def test_a_well_exposed_bright_scan_remains_neutral_with_highlight_detail():
    image = _neutral_ramp()
    out = enhance_slide(_slide(image), PLAIN, 600).astype(np.float64) / FULL
    assert np.array_equal(out[..., 0], out[..., 1])
    assert np.array_equal(out[..., 1], out[..., 2])
    assert np.all(np.diff(out[0, :, 0]) >= 0)
    assert 0.9 < out[0, -1, 0] < 1.0


def test_shadows_outside_the_interior_are_not_clipped():
    """The darkest content of a neutral frame may lie at its edges, outside the interior."""
    image = _neutral_ramp()
    assert np.array_equal(subtract_black(image), image)


def test_a_scene_coloured_shadow_is_not_taken_for_a_black_level():
    image = _dark_linear_scene()
    image[..., 0] = np.minimum(image[..., 0].astype(np.int64) + int(0.2 * FULL), FULL)
    out = subtract_black(image)
    taken = (image[..., 0].astype(np.int64) - out[..., 0]).max() / FULL
    assert taken <= MAX_BLACK_OFFSET + 1e-4


def test_negative_mode_is_the_unchanged_inversion_path():
    """An inverted negative is already tone mapped: same steps and gamma floor as before."""
    t = (np.arange(H * W, dtype=np.float64) + 0.5) / (H * W)
    density = np.random.default_rng(5).permuted(2.0 * t**4).reshape(H, W)
    density = np.stack([density, density + 0.05, density], axis=2)  # inverts dark, offset
    image = np.rint(10.0 ** (-density) * FULL).astype(np.uint16)
    plain = ProcessOptions(
        mode="negative", remove_dust=False, sharpen_amount=0.0, grain_strength=0.0
    )
    out = enhance_slide(_slide(image), plain, 600)
    assert np.array_equal(out, restore_color(auto_exposure(invert_negative(image))))


def _srgb_encode(linear: np.ndarray) -> np.ndarray:
    return np.where(linear <= 0.0031308, 12.92 * linear, 1.055 * linear ** (1 / 2.4) - 0.055)


def test_an_imported_finished_image_takes_the_unchanged_tone_path():
    """A dark, low-key JPEG from other software is already tone mapped: no linear treatment."""
    image = np.rint(_srgb_encode(_dark_linear_scene() / FULL * 0.05) * FULL).astype(np.uint16)
    out = enhance_slide(_slide(image), PLAIN, 600, linear=False)
    assert np.array_equal(out, restore_color(auto_exposure(image)))
    as_scan = enhance_slide(_slide(image), PLAIN, 600)
    assert _median(out) < _median(as_scan) - 0.08  # the scan path would lift it much further


def test_the_import_command_uses_the_finished_image_path(monkeypatch, tmp_path):
    import tifffile

    from opticfilm_linux import imports
    from opticfilm_linux.output import OutputSpec

    seen = []
    real = imports.enhance_slide

    def spy(slide, options, dpi, **kwargs):
        seen.append(kwargs.get("linear", True))
        return real(slide, options, dpi, **kwargs)

    monkeypatch.setattr(imports, "enhance_slide", spy)
    tifffile.imwrite(tmp_path / "a.tif", _dark_linear_scene())
    imports.process_images([tmp_path / "a.tif"], PLAIN, OutputSpec(directory=tmp_path / "o"), 600)
    assert seen == [False]


def test_the_scanners_black_offset_goes_even_with_colour_restoration_off():
    """``color_strength`` 0 turns off the fade/cast stretch, not the scanner's own black level."""
    options = ProcessOptions(
        remove_dust=False, sharpen_amount=0.0, grain_strength=0.0, color_strength=0.0
    )
    out = enhance_slide(_slide(_dark_linear_scene()), options, 600)
    assert _balance(out) < 1.03
