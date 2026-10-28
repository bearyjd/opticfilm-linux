import cv2
import numpy as np
import pytest

from opticfilm_linux.pipeline.dust import defect_mask, remove_dust
from synthetic import dust_centers, make_sweep, truth_boxes

DPI = 600
SEED = 4
DUST = 12


def _apertures(dust: int):
    """Per aperture: (rgb crop, ir crop, clean rgb crop, speck centres in crop coordinates)."""
    dusty = make_sweep(dpi=DPI, dust=dust, seed=SEED)
    clean = make_sweep(dpi=DPI, dust=0, seed=SEED)
    centers = dust_centers(DPI, dust=dust, seed=SEED)
    out = []
    for box in truth_boxes(DPI):
        rows = slice(box.y, box.y + box.h)
        local = [(y - box.y, x) for y, x in centers if box.y <= y < box.y + box.h]
        out.append((dusty.rgb[rows], dusty.ir[rows], clean.rgb[rows], local))
    return out


def _near(shape, centers, radius):
    marks = np.zeros(shape, np.uint8)
    for y, x in centers:
        cv2.circle(marks, (x, y), radius, 1, -1)
    return marks.astype(bool)


def _masked_error(image: np.ndarray, clean: np.ndarray, mask: np.ndarray) -> float:
    return float(np.abs(image.astype(np.float64) - clean)[mask].sum())


def test_defect_mask_finds_synthetic_specks():
    hit = total = false_pixels = area = 0
    for _, ir, _, centers in _apertures(DUST):
        mask = defect_mask(ir)
        hit += sum(bool(mask[y, x]) for y, x in centers)
        total += len(centers)
        false_pixels += int((mask & ~_near(mask.shape, centers, 12)).sum())
        area += mask.size
    assert total == DUST
    assert hit / total >= 0.95
    assert false_pixels / area < 0.001


def test_remove_dust_reduces_error_inside_mask():
    before = after = count = 0.0
    for rgb, ir, clean, _ in _apertures(DUST):
        mask = defect_mask(ir)
        out = remove_dust(rgb, ir, dpi=DPI)
        before += _masked_error(rgb, clean, mask)
        after += _masked_error(out, clean, mask)
        count += mask.sum()
    assert count > 0
    assert after < 0.3 * before


def test_clean_ir_gives_empty_mask_and_identical_output():
    for rgb, ir, _, _ in _apertures(0):
        assert not defect_mask(ir).any()
        assert np.array_equal(remove_dust(rgb, ir, dpi=DPI), rgb)


def test_no_ir_returns_copy():
    rgb = _apertures(DUST)[0][0]
    out = remove_dust(rgb, None, dpi=DPI)
    assert np.array_equal(out, rgb) and out is not rgb


def test_repair_does_not_depend_on_where_the_image_is_cut():
    """The same defect on the same texture is repaired identically wherever it falls."""
    rng = np.random.default_rng(5)
    base = np.clip(rng.normal(0.5, 0.12, (1500, 300, 1)) * np.ones((1, 1, 3)), 0, 1)
    base = (base * 65535).astype(np.uint16)

    def repaired(first_row: int) -> np.ndarray:
        rgb = base[first_row : first_row + 1400].copy()
        ir = np.full(rgb.shape[:2], int(0.9 * 65535), np.uint16)
        top = 1000 - first_row
        rgb[top : top + 60, 140:144] = 0
        ir[top : top + 60, 140:144] = int(0.2 * 65535)
        out = remove_dust(rgb, ir, dpi=3600)
        return out[top - 30 : top + 90].astype(np.int64)

    # at first_row=0 the defect straddles row 1024; at 100 it sits well clear of it
    assert np.abs(repaired(0) - repaired(100)).max() <= 0.001 * 65535


def test_input_is_not_modified():
    rgb, ir, _, _ = _apertures(DUST)[0]
    rgb_before, ir_before = rgb.copy(), ir.copy()
    remove_dust(rgb, ir, dpi=DPI)
    assert np.array_equal(rgb, rgb_before) and np.array_equal(ir, ir_before)


def test_defect_mask_for_scales_with_dpi_like_remove_dust():
    from opticfilm_linux.pipeline.dust import defect_mask_for

    ir = _apertures(DUST)[0][1]
    assert np.array_equal(defect_mask_for(ir, 600), defect_mask(ir, dilate_px=2, median_px=21))
    assert defect_mask_for(ir, 1200).sum() >= defect_mask_for(ir, 600).sum()


# Real infrared and colour planes register to 0 px, so the mask only needs to cover a speck's
# soft edge: its dilation and the inpaint radius must not grow with the scan resolution.
SPECK_STEP = 40


def _one_pixel_specks(size: int, step: int = SPECK_STEP) -> tuple[np.ndarray, list]:
    """A clean infrared plane with a 1-px dark speck every ``step`` pixels."""
    ir = np.full((size, size), 58_000, np.uint16)
    steps = range(step, size - step, step)
    centers = [(y, x) for y in steps for x in steps]
    for y, x in centers:
        ir[y, x] = 15_000
    return ir, centers


def _covered_per_speck(dpi: int) -> float:
    from opticfilm_linux.pipeline.dust import defect_mask_for

    ir, centers = _one_pixel_specks(1200)
    mask = defect_mask_for(ir, dpi)
    assert all(mask[y, x] for y, x in centers)
    return mask.sum() / len(centers)


def test_mask_around_a_one_pixel_speck_stays_small_at_3600_dpi():
    assert _covered_per_speck(3600) <= 25


def test_mask_coverage_does_not_grow_with_dpi():
    at_600 = _covered_per_speck(600)
    assert _covered_per_speck(3600) <= 1.5 * at_600
    assert _covered_per_speck(7200) <= 1.5 * at_600


def _ramp_with_blob() -> tuple[np.ndarray, np.ndarray, tuple[slice, slice]]:
    """A smooth two-axis 16-bit ramp with a 40x40 defect in the middle."""
    yy, xx = np.mgrid[0:200, 0:200].astype(np.float64)
    level = 4_000 + 150 * yy + 90 * xx  # steep: neighbouring repaired pixels differ in level
    rgb = np.repeat(level[..., None], 3, axis=2).astype(np.uint16)
    ir = np.full((200, 200), 58_000, np.uint16)
    core = (slice(80, 120), slice(80, 120))
    rgb[core] = 1_000
    ir[core] = 15_000
    return rgb, ir, core


def test_repair_keeps_16_bit_precision():
    from opticfilm_linux.pipeline.dust import defect_mask_for

    rgb, ir, _ = _ramp_with_blob()
    mask = defect_mask_for(ir, 3600)
    repaired = remove_dust(rgb, ir, dpi=3600)[..., 1][mask]
    assert np.unique(repaired).size > 256
    assert not np.all(repaired % 257 == 0)


def test_repair_follows_the_ramp_under_the_defect():
    rgb, ir, core = _ramp_with_blob()
    yy, xx = np.mgrid[core]
    truth = 4_000 + 150 * yy + 90 * xx
    repaired = remove_dust(rgb, ir, dpi=3600)[core][..., 1].astype(np.float64)
    # a 3 px radius fills a 40 px hole from its rim: close to the ramp, not exact (left
    # unrepaired, the error would be about 2.5x the ramp's range)
    assert np.abs(repaired - truth).mean() < 0.15 * np.ptp(truth)


def _inpaint_work(monkeypatch, ir: np.ndarray, rgb: np.ndarray, dpi: int) -> int:
    """Masked pixels times radius squared over every cv2.inpaint call: inpainting's cost."""
    from opticfilm_linux.pipeline import dust

    work = [0]
    real = cv2.inpaint

    def counting(src, mask, radius, flags):
        work[0] += int(np.count_nonzero(mask)) * radius * radius
        return real(src, mask, radius, flags)

    monkeypatch.setattr(dust.cv2, "inpaint", counting)
    monkeypatch.setattr(dust, "INPAINT_WORKERS", 1)
    dust.remove_dust(rgb, ir, dpi=dpi)
    return work[0]


def test_inpaint_work_for_2000_specks_at_3600_dpi_is_bounded(monkeypatch):
    size = 45 * SPECK_STEP  # about 1900 specks
    ir, centers = _one_pixel_specks(size)
    rgb = np.full((size, size, 3), 30_000, np.uint16)
    work = _inpaint_work(monkeypatch, ir, rgb, 3600)
    assert len(centers) > 1800
    # a 17 px mask (21 with room), a 3 px radius and three channels, with 2x slack; the 12 px
    # dilation and 18 px radius this replaced cost about 1.4 million per speck
    assert work / len(centers) < 2 * 21 * 3 * 3 * 3


def test_repair_of_a_flat_area_keeps_its_exact_16_bit_level():
    """Losing the low byte, on the way in or out, moves each of these levels by more than 50
    (TELEA itself rounds by a few)."""
    rgb = np.full((120, 120, 3), (30_123, 12_400, 50_001), np.uint16)
    ir = np.full((120, 120), 58_000, np.uint16)
    rgb[50:70, 50:70] = 1_000
    ir[50:70, 50:70] = 15_000
    out = remove_dust(rgb, ir, dpi=3600)
    assert np.abs(out[50:70, 50:70].astype(np.int64) - (30_123, 12_400, 50_001)).max() <= 8


def _blurred_specks(dpi: int) -> tuple[np.ndarray, np.ndarray]:
    """Specks whose size scales with dpi (2-4 px at 600 dpi, as in ``make_sweep``) and whose edge
    is softened by the scanner's optics: a blur of a fixed size in sensor pixels."""
    size = 60 * dpi // 600 + 200
    rgb = np.full((size, size), 30_000, np.float32)
    ir = np.full((size, size), 58_000, np.float32)
    for k, radius_at_600 in enumerate((2, 3, 4)):
        radius = round(radius_at_600 * dpi / 600)
        center = (size // 2 + (k - 1) * size // 3, size // 2)
        cv2.circle(rgb, center, radius, 3_000, -1)
        cv2.circle(ir, center, radius, 20_000, -1)
    rgb, ir = (cv2.GaussianBlur(plane, (0, 0), 0.8) for plane in (rgb, ir))
    return np.repeat(rgb[..., None], 3, axis=2).astype(np.uint16), ir.astype(np.uint16)


@pytest.mark.parametrize("dpi", [600, 3600, 7200])
def test_no_dark_halo_is_left_around_a_repaired_speck(dpi):
    rgb, ir = _blurred_specks(dpi)
    out = remove_dust(rgb, ir, dpi=dpi)
    assert 30_000 - int(out.min()) < 300  # 1% of the background; a 1 px dilation leaves ~700
