import tracemalloc

import numpy as np
import pytest

from opticfilm_linux.pipeline import arrays
from opticfilm_linux.pipeline.detail import reduce_grain, sharpen
from opticfilm_linux.pipeline.tone import auto_exposure, invert_negative, restore_color

STEPS = [auto_exposure, restore_color, invert_negative, sharpen, reduce_grain]


def _image(rows: int = 1500, cols: int = 1500) -> np.ndarray:
    rng = np.random.default_rng(0)
    base = np.linspace(0.1, 0.9, cols)[None, :, None] * np.ones((rows, 1, 3))
    noisy = base + rng.normal(0, 0.03, (rows, cols, 3))
    return np.clip(noisy * 65535, 0, 65535).astype(np.uint16)


@pytest.mark.parametrize("step", STEPS, ids=lambda s: s.__name__)
def test_peak_memory_stays_near_the_input_size(step):
    image = _image()
    tracemalloc.start()
    step(image)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 3.0 * image.nbytes


@pytest.mark.parametrize("step", STEPS, ids=lambda s: s.__name__)
def test_strips_give_the_same_result_as_one_pass(step, monkeypatch):
    image = _image(rows=300, cols=200)
    monkeypatch.setattr(arrays, "STRIP_ROWS", 10_000)
    whole = step(image).astype(np.int32)
    monkeypatch.setattr(arrays, "STRIP_ROWS", 37)
    stripped = step(image).astype(np.int32)
    assert np.abs(whole - stripped).max() <= 1
