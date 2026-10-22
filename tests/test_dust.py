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


def test_tiling_leaves_no_seam():
    rgb, ir, _, _ = _apertures(DUST)[0]
    small = remove_dust(rgb, ir, dpi=DPI, tile=256).astype(np.int64)
    large = remove_dust(rgb, ir, dpi=DPI, tile=4096).astype(np.int64)
    seams = [r for r in range(256, rgb.shape[0], 256)]
    diff = np.abs(small - large)
    assert max(diff[r - 2 : r + 2].max() for r in seams) <= 0.02 * 65535


def test_input_is_not_modified():
    rgb, ir, _, _ = _apertures(DUST)[0]
    rgb_before, ir_before = rgb.copy(), ir.copy()
    remove_dust(rgb, ir, dpi=DPI)
    assert np.array_equal(rgb, rgb_before) and np.array_equal(ir, ir_before)
