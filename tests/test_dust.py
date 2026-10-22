import cv2
import numpy as np

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
