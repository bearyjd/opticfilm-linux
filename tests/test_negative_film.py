"""Negative-film handling on synthetic data only: inversion, base colour, dust, splitting,
blank windows, the tray pipeline and export."""

import numpy as np
import pytest
import tifffile
from PIL import Image

from opticfilm_linux.batch.enhance import enhance_tray, options_for, reenhance_slide
from opticfilm_linux.batch.export import export_batch
from opticfilm_linux.batch.model import BatchDefaults, SlideSettings, SlideStatus, TrayStage
from opticfilm_linux.batch.review import accept_all
from opticfilm_linux.output import OutputSpec
from opticfilm_linux.pipeline.run import ProcessOptions, enhance_slide, process_sweep
from opticfilm_linux.pipeline.split import split_slides
from opticfilm_linux.pipeline.tone import invert_negative
from opticfilm_linux.sweepstore import load_sweep
from synthetic import (
    FULL,
    NEGATIVE_DENSITY,
    dust_centers,
    make_negative_sweep,
    make_sweep,
    truth_boxes,
)
from test_enhance_tray import _set_slide, _store, _tray

NEUTRAL = (1.0, 1.0, 1.0)
PLAIN = {"remove_dust": False, "grain_strength": 0.0, "sharpen_amount": 0.0}


def _ramp(h: int = 64, w: int = 256) -> np.ndarray:
    """A neutral positive scene, dark to bright left to right, as floats in [0.1, 0.9]."""
    row = np.linspace(0.1, 0.9, w)
    return np.broadcast_to(row[None, :, None], (h, w, 3)).copy()


def _as_negative(scene: np.ndarray, base) -> np.ndarray:
    transmittance = np.array(base) * 10.0 ** (-NEGATIVE_DENSITY * scene)
    return np.rint(transmittance * FULL).astype(np.uint16)


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.corrcoef(a.astype(np.float64).ravel(), b.astype(np.float64).ravel())[0, 1])


def _luma(rgb: np.ndarray) -> np.ndarray:
    return rgb.astype(np.float64).mean(axis=2) / FULL


def _after_preview(store) -> np.ndarray:
    with Image.open(store.preview_path(1, 1, "after")) as image:
        return np.asarray(image.convert("L"), np.float64) / 255.0


def test_orange_base_is_removed_so_a_neutral_scene_stays_neutral():
    scene = _ramp()
    plain = invert_negative(_as_negative(scene, NEUTRAL)).astype(np.float64) / FULL
    orange = invert_negative(_as_negative(scene, ORANGE)).astype(np.float64) / FULL
    channel_means = orange.mean(axis=(0, 1))
    assert channel_means.max() - channel_means.min() < 0.02
    assert np.abs(orange - plain).max() < 0.02


ORANGE = (1.0, 0.6, 0.35)


@pytest.mark.parametrize("base", [ORANGE, (0.8, 0.8, 0.8), (0.5, 0.9, 0.7)])
def test_a_denser_negative_gives_a_brighter_output_and_the_range_is_stretched(base):
    out = invert_negative(_as_negative(_ramp(), base))[0].astype(np.int64)
    assert (np.diff(out, axis=0) >= 0).all()  # the ramp's bright end is its densest
    assert ((out[-1] - out[0]) > 0.9 * FULL).all()


def test_inversion_survives_clipped_shadows_and_highlights():
    negative = _as_negative(_ramp(), NEUTRAL)
    negative[:, :16] = FULL  # fully clear film
    negative[:, -16:] = 0  # opaque
    out = invert_negative(negative)
    assert out.dtype == np.uint16 and out.shape == negative.shape
    assert (out[:, :16] == 0).all() and (out[:, -16:] == FULL).all()
    interior = out[:, 16:-16].astype(np.float64) / FULL
    assert interior.max() - interior.min() > 0.2 and 0 < interior.mean() < 1


def test_negative_mode_recovers_the_scene_and_positive_mode_does_not():
    pos = split_slides(make_sweep(dpi=600))[0]
    neg = split_slides(make_negative_sweep(dpi=600))[0]
    assert pos.rgb.shape == neg.rgb.shape
    # The synthetic positive encodes a finished scene, like the density-inverted negative.
    scene = _luma(enhance_slide(pos, ProcessOptions(**PLAIN), 600, linear=False))
    inverted = _luma(enhance_slide(neg, ProcessOptions(mode="negative", **PLAIN), 600))
    uninverted = _luma(enhance_slide(neg, ProcessOptions(mode="positive", **PLAIN), 600))
    assert _corr(inverted, scene) > 0.9
    assert _corr(uninverted, scene) < 0.0
    # Channel by channel too, so a swapped or mixed channel cannot hide in the average.
    fixed = enhance_slide(neg, ProcessOptions(mode="negative", **PLAIN), 600)
    reference = enhance_slide(pos, ProcessOptions(**PLAIN), 600, linear=False)
    for channel in range(3):
        assert _corr(fixed[..., channel], reference[..., channel]) > 0.9


def test_negative_output_has_no_colour_cast_from_the_base():
    options = ProcessOptions(mode="negative", **PLAIN)

    def means(base):
        slide = split_slides(make_negative_sweep(dpi=600, base=base))[0]
        return enhance_slide(slide, options, 600).astype(np.float64).mean(axis=(0, 1)) / FULL

    assert np.abs(means(ORANGE) - means(NEUTRAL)).max() < 0.03


def test_negative_dust_is_repaired_before_inversion_not_turned_into_white_spots():
    dpi, dust, seed = 600, 10, 3
    slides = split_slides(make_negative_sweep(dpi=dpi, dust=dust, seed=seed))
    repair = ProcessOptions(mode="negative", grain_strength=0.0, sharpen_amount=0.0)
    keep = ProcessOptions(
        mode="negative", remove_dust=False, grain_strength=0.0, sharpen_amount=0.0
    )
    tops = [b.y for b in truth_boxes(dpi)]
    repaired_worst = unrepaired_worst = 0.0
    checked = 0
    for y, x in dust_centers(dpi, dust, seed=seed):
        slide = slides[max(k for k, top in enumerate(tops) if top <= y)]
        yy, xx = y - slide.box.y, x - slide.box.x
        if not (6 <= yy < slide.rgb.shape[0] - 6 and 6 <= xx < slide.rgb.shape[1] - 6):
            continue
        checked += 1
        fixed = _luma(enhance_slide(slide, repair, dpi))
        raw = _luma(enhance_slide(slide, keep, dpi))
        ring = np.r_[fixed[yy - 6 : yy + 7, xx - 6], fixed[yy - 6 : yy + 7, xx + 6]].mean()
        repaired_worst = max(repaired_worst, abs(fixed[yy, xx] - ring))
        unrepaired_worst = max(unrepaired_worst, abs(raw[yy, xx] - ring))
    assert checked
    assert unrepaired_worst > 0.3  # without repair a speck is a bright spot after inversion
    assert repaired_worst < 0.15


def test_negative_dust_repair_lands_near_the_dust_free_picture_and_leaves_the_rest():
    dpi, seed = 600, 3
    clean = split_slides(make_negative_sweep(dpi=dpi, seed=seed))
    dusty = split_slides(make_negative_sweep(dpi=dpi, dust=10, seed=seed))
    options = ProcessOptions(mode="negative", grain_strength=0.0, sharpen_amount=0.0)
    worst_share = 0.0
    for clean_slide, dusty_slide in zip(clean, dusty, strict=True):
        want = _luma(enhance_slide(clean_slide, options, dpi))
        got = _luma(enhance_slide(dusty_slide, options, dpi))
        assert np.abs(got - want).mean() < 0.01  # most pixels are untouched
        worst_share = max(worst_share, float((np.abs(got - want) > 0.1).mean()))
        assert _corr(got, want) > 0.99
    assert worst_share < 0.005


def test_negative_windows_split_by_infrared_where_the_mount_is_opaque():
    slides = split_slides(make_negative_sweep(dpi=600))
    assert [s.box.y for s in slides] == pytest.approx([b.y for b in truth_boxes(600)], abs=2)


def test_dense_negative_still_splits_without_infrared():
    slides = split_slides(make_negative_sweep(dpi=600, ir=False))
    assert [s.box.y for s in slides] == pytest.approx([b.y for b in truth_boxes(600)], abs=4)


def test_clear_windows_in_a_negative_holder_are_skipped_as_blank(tmp_path):
    sweep = make_negative_sweep(dpi=600, filled=(True, False, True, False))
    result = process_sweep(sweep, ProcessOptions(mode="negative"), OutputSpec(directory=tmp_path))
    assert result.blank_positions == (2, 4) and len(result.written) == 2


def test_negative_sweep_without_infrared_processes(tmp_path):
    sweep = make_negative_sweep(dpi=600, ir=False)
    result = process_sweep(sweep, ProcessOptions(mode="negative"), OutputSpec(directory=tmp_path))
    assert len(result.written) == 4


def test_negative_output_is_full_range_uint16(tmp_path):
    sweep = make_negative_sweep(dpi=600, dust=8)
    result = process_sweep(sweep, ProcessOptions(mode="negative"), OutputSpec(directory=tmp_path))
    for path in result.written:
        image = tifffile.imread(path)
        assert image.dtype == np.uint16 and image.ndim == 3
        assert image.max() - image.min() > 0.5 * FULL


def test_negative_tray_reaches_review_with_inverted_previews(tmp_path):
    store = _store(tmp_path, make_negative_sweep(dpi=600), mode="negative")
    enhance_tray(store, 1)
    assert _tray(store).stage is TrayStage.REVIEW and len(_tray(store).slides) == 4
    reference = _luma(
        enhance_slide(
            split_slides(make_sweep(dpi=600))[0], ProcessOptions(**PLAIN), 600, linear=False
        )
    )
    preview = _after_preview(store)
    small = Image.fromarray((reference * 255).astype(np.uint8)).resize(preview.shape[::-1])
    assert _corr(preview, np.asarray(small, np.float64) / 255.0) > 0.8


def test_a_good_negative_is_not_flagged(tmp_path):
    store = _store(tmp_path, make_negative_sweep(dpi=600), mode="negative")
    enhance_tray(store, 1)
    assert all(s.status is SlideStatus.ACCEPTED for s in _tray(store).slides)


def test_reenhancing_a_negative_slide_with_new_settings_stays_inverted(tmp_path):
    store = _store(tmp_path, make_negative_sweep(dpi=600), mode="negative")
    enhance_tray(store, 1)
    first = _after_preview(store)
    _set_slide(store, 1, settings=SlideSettings(brightness_ev=1.0), owner_decided=True)
    reenhance_slide(store, 1, 1)
    second = _after_preview(store)
    assert second.mean() > first.mean() + 0.02  # the new setting took effect
    assert _corr(second, first) > 0.8  # and the picture is still the inverted scene


def test_options_for_carries_the_negative_mode_with_slide_settings():
    options = options_for(BatchDefaults(), "negative", SlideSettings(brightness_ev=1.0), 1)
    assert options.mode == "negative" and options.brightness_ev == 1.0


def test_export_of_a_negative_tray_is_the_inverted_picture(tmp_path):
    store = _store(tmp_path, make_negative_sweep(dpi=600), mode="negative")
    enhance_tray(store, 1)
    accept_all(store)
    result = export_batch(store, tmp_path / "out")
    assert len(result.written) == 4
    manifest = store.snapshot()
    sweep = load_sweep(store.sweep_dir(1, 1))
    slide = split_slides(sweep)[0]
    settings = manifest.trays[0].slides[0].settings
    negative = options_for(manifest.defaults, "negative", settings, 1)
    positive = options_for(manifest.defaults, "positive", settings, 1)
    exported = tifffile.imread(result.written[0])
    assert np.array_equal(exported, enhance_slide(slide, negative, sweep.dpi))
    assert not np.array_equal(exported, enhance_slide(slide, positive, sweep.dpi))
