import numpy as np

from opticfilm_linux.batch.flags import compute_flags
from opticfilm_linux.batch.model import Flag

FULL = 65535


def _flat(level: float, h: int = 600, w: int = 800) -> np.ndarray:
    return np.full((h, w, 3), np.uint16(level * FULL), np.uint16)


def _gradient() -> np.ndarray:
    ramp = np.linspace(0.3, 0.7, 800)[None, :, None] * np.ones((600, 1, 3))
    return (ramp * FULL).astype(np.uint16)


def _speckled_ir(count: int) -> np.ndarray:
    ir = np.full((600, 800), np.uint16(0.9 * FULL), np.uint16)
    rng = np.random.default_rng(3)
    for y, x in zip(rng.integers(10, 590, count), rng.integers(10, 790, count), strict=True):
        ir[y : y + 5, x : x + 5] = np.uint16(0.3 * FULL)
    return ir


def test_dark_image_is_flagged_too_dark():
    assert Flag.TOO_DARK in compute_flags(_flat(0.05), None, 600)


def test_bright_image_is_flagged_too_bright():
    assert Flag.TOO_BRIGHT in compute_flags(_flat(0.95), None, 600)


def test_heavy_clipping_is_flagged():
    image = _gradient()
    image[:60] = FULL  # 10 % pure white
    assert Flag.CLIPPING in compute_flags(image, None, 600)


def test_large_defect_area_in_ir_is_flagged_dust():
    assert Flag.DUST in compute_flags(_gradient(), _speckled_ir(600), 600)


def test_a_few_specks_are_not_flagged_dust():
    assert Flag.DUST not in compute_flags(_gradient(), _speckled_ir(5), 600)


def test_no_ir_never_flags_dust():
    assert Flag.DUST not in compute_flags(_gradient(), None, 600)


def test_clean_midtone_image_has_no_flags():
    ir = np.full((600, 800), np.uint16(0.9 * FULL), np.uint16)
    assert compute_flags(_gradient(), ir, 600) == ()


def test_flags_come_in_fixed_order():
    image = _flat(0.02)
    image[:30] = FULL
    assert compute_flags(image, None, 600) == (Flag.TOO_DARK, Flag.CLIPPING)


def test_infrared_dips_on_visible_edges_are_not_counted_as_dust():
    from test_dust_edges import _edge_frame

    rgb, ir, _ = _edge_frame(specks=False)
    assert Flag.DUST in compute_flags(rgb, ir, 600)
    assert Flag.DUST not in compute_flags(rgb, ir, 600, visible=rgb)
