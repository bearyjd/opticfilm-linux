"""Pins the fixes that keep 3600 and 7200 dpi trays within memory and time budgets."""

import time
import tracemalloc

import cv2
import numpy as np
import pytest

import reference_pipeline
from opticfilm_linux.pipeline import arrays, dust
from opticfilm_linux.pipeline.detail import reduce_grain, sharpen
from opticfilm_linux.pipeline.dust import remove_dust
from opticfilm_linux.pipeline.tone import auto_exposure, invert_negative, restore_color


def _dusty(rows: int = 1200, cols: int = 1500) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(1)
    rgb = rng.integers(20_000, 40_000, (rows, cols, 3), dtype=np.uint16)
    ir = np.full((rows, cols), 58_000, np.uint16)
    for y, x in rng.integers(50, min(rows, cols) - 50, (8, 2)):
        ir[y - 6 : y + 6, x - 6 : x + 6] = 15_000
    return rgb, ir


def test_dust_removal_never_converts_the_whole_slide_to_8_bit():
    rgb, ir = _dusty()
    tracemalloc.start()
    out = remove_dust(rgb, ir, dpi=600)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert not np.array_equal(out, rgb)  # specks were repaired
    # the output copy plus a few mask planes (1/6 of the image each); a full-image 8-bit copy
    # and its 16-bit shift temporary would add 1.5x on top
    assert peak < 1.8 * rgb.nbytes


STEPS = [auto_exposure, restore_color, invert_negative, sharpen, reduce_grain]


@pytest.mark.parametrize("step", STEPS, ids=lambda s: s.__name__)
def test_strips_in_parallel_give_byte_identical_results(step, monkeypatch):
    image = _dusty(rows=700, cols=300)[0]
    monkeypatch.setattr(arrays, "MAX_WORKERS", 1)
    serial = step(image)
    monkeypatch.setattr(arrays, "MAX_WORKERS", 6)
    monkeypatch.setattr(arrays, "STRIPS_PER_WORKER", 1)
    assert np.array_equal(step(image), serial)


def test_parallel_strips_keep_float_temporaries_below_the_image_size(monkeypatch):
    monkeypatch.setattr(arrays, "MAX_WORKERS", 8)  # as on a machine with 8 or more cores
    assert arrays.strip_workers(1) == 1  # a small image runs in the caller's thread
    assert arrays.strip_workers(12) == 1
    assert arrays.strip_workers(28) == 3  # a 3600 dpi slide: 3615 rows in 128-row strips
    assert arrays.strip_workers(10_000) == arrays.MAX_WORKERS


def test_a_failing_strip_raises_in_the_caller(monkeypatch):
    monkeypatch.setattr(arrays, "MAX_WORKERS", 4)
    monkeypatch.setattr(arrays, "STRIPS_PER_WORKER", 1)

    def boom(x: np.ndarray) -> np.ndarray:
        raise RuntimeError("strip failed")

    with pytest.raises(RuntimeError, match="strip failed"):
        arrays.map_strips(np.zeros((1000, 10, 3), np.uint16), boom)


def test_defects_repaired_in_parallel_match_the_serial_repair(monkeypatch):
    rgb, ir = _dusty(rows=600, cols=600)
    rng = np.random.default_rng(5)
    rgb = rng.integers(0, 65_535, rgb.shape, dtype=np.uint16)  # texture, so patches differ
    ir[100:200, 100:110] = ir[190:200, 100:200] = 15_000  # an L ...
    ir[150:160, 150:160] = 15_000  # ... with a speck inside its bounding box
    monkeypatch.setattr(dust, "INPAINT_WORKERS", 1)
    serial = remove_dust(rgb, ir, dpi=600)
    monkeypatch.setattr(dust, "INPAINT_WORKERS", 4)
    assert np.array_equal(remove_dust(rgb, ir, dpi=600), serial)


def test_overlapping_patches_are_written_back_in_contour_order(monkeypatch):
    """Where bounding boxes overlap, the later contour wins, with any number of threads."""
    rgb, ir = _dusty(rows=300, cols=300)
    ir[:] = 58_000
    ir[50:150, 50:60] = ir[140:150, 50:150] = 15_000  # an L ...
    ir[100:110, 100:110] = 15_000  # ... with a speck inside its bounding box
    real = dust._repair
    order: list[tuple[int, int, int, int]] = []

    def stamped(rgb, mask, mask8, box, radius):
        core, defect, values = real(rgb, mask, mask8, box, radius)
        order.append(box)
        time.sleep(0.05 if box[2] > 50 else 0)  # the big L finishes last on a thread
        return core, defect, np.full_like(values, box[2])  # a value that names the box

    monkeypatch.setattr(dust, "_repair", stamped)
    monkeypatch.setattr(dust, "INPAINT_WORKERS", 1)
    serial = remove_dust(rgb, ir, dpi=600)
    monkeypatch.setattr(dust, "INPAINT_WORKERS", 4)
    monkeypatch.setattr(dust, "PATCH_BUDGET_BYTES", 1 << 30)  # all patches in flight at once
    assert np.array_equal(remove_dust(rgb, ir, dpi=600), serial)
    assert len(set(order)) == 2 and len(np.unique(serial[100:110, 100:110])) == 1


@pytest.mark.parametrize("step", STEPS, ids=lambda s: s.__name__)
def test_threaded_strips_keep_peak_memory_near_the_input_size(step, monkeypatch):
    monkeypatch.setattr(arrays, "MAX_WORKERS", 8)
    image = _dusty(rows=3100, cols=600)[0]  # 25 strips: 3 in flight at once
    assert arrays.strip_workers(25) == 3
    tracemalloc.start()
    step(image)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 3.0 * image.nbytes


@pytest.mark.parametrize("step", [sharpen, reduce_grain], ids=lambda s: s.__name__)
def test_threads_follow_opencv_s_optimisation_switch(step, monkeypatch):
    """cv2.setUseOptimized is per thread: worker threads would ignore the caller's choice."""
    image = _dusty(rows=1100, cols=300)[0]
    monkeypatch.setattr(arrays, "STRIPS_PER_WORKER", 1)
    monkeypatch.setattr(arrays, "MAX_WORKERS", 4)
    was = cv2.useOptimized()
    cv2.setUseOptimized(False)
    try:
        with monkeypatch.context() as frozen:
            reference_pipeline.use_reference(frozen)
            from opticfilm_linux.pipeline import detail

            serial = getattr(detail, step.__name__)(image)
        assert np.array_equal(step(image), serial)
    finally:
        cv2.setUseOptimized(was)


def _scratched(size: int = 1000) -> tuple[np.ndarray, np.ndarray]:
    """Eight separate diagonal scratches: eight nearly full-image bounding boxes."""
    rng = np.random.default_rng(9)
    rgb = rng.integers(0, 65_535, (size, size, 3), dtype=np.uint16)
    ir = np.full((size, size), 58_000, np.uint16)
    for k in range(8):
        offset = (k - 4) * 40
        for i in range(size):
            j = i + offset
            if 0 <= j < size:
                ir[i, j] = 15_000
    return rgb, ir


def _peak(func, *args, **kwargs) -> tuple[np.ndarray, int]:
    tracemalloc.start()
    out = func(*args, **kwargs)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return out, peak


def test_concurrent_patches_are_bounded_by_bytes_not_by_count(monkeypatch):
    monkeypatch.setattr(dust, "INPAINT_WORKERS", 8)
    rgb, ir = _scratched()
    _, old_peak = _peak(reference_pipeline.remove_dust, rgb, ir, dpi=600)
    new, new_peak = _peak(remove_dust, rgb, ir, dpi=600)
    monkeypatch.setattr(dust, "INPAINT_WORKERS", 1)
    assert np.array_equal(new, remove_dust(rgb, ir, dpi=600))  # 8-bit reference repairs differ
    assert new_peak < 1.5 * old_peak
