"""Pins the pipeline's 600 dpi outputs byte for byte, so performance work cannot change pixels.

The hashes were taken before any optimisation (numpy 2.5, OpenCV 5.0 from the lockfile). If a
dependency update changes them, check the images by eye before re-pinning.
"""

import hashlib

import numpy as np
import pytest

from opticfilm_linux.batch.flags import compute_flags
from opticfilm_linux.batch.model import Flag
from opticfilm_linux.batch.previews import make_preview
from opticfilm_linux.pipeline import arrays, dust
from opticfilm_linux.pipeline.run import ProcessOptions, enhance_slide
from opticfilm_linux.pipeline.split import split_slides
from synthetic import make_sweep

POSITIVE = ProcessOptions()
NEGATIVE = ProcessOptions(mode="negative", brightness_ev=0.5, rotation=90)
# (enhanced, after preview, before preview) per slide
PINNED = {
    "positive": [
        ("0dd38874823d2313", "4365a694618f0645", "6a5debe78ef169c9"),
        ("c0e1feca98e42f17", "8aa14f08224bf13b", "fbdcbbcee6223a2d"),
        ("ed353fad9caf9fa9", "357d070dc4297d69", "89b723526263a2c1"),
        ("2d0b37051eb2d699", "28e1f0b4d3748143", "9e0107edaf8d2a53"),
    ],
    "negative": [
        ("2728e8474b35b706", "bbb63b7f6b8aeecd", "6a5debe78ef169c9"),
        ("468dfe091d46a410", "5f8c409b114f93a6", "fbdcbbcee6223a2d"),
        ("bc2381150777b364", "9898299cb2489d31", "89b723526263a2c1"),
        ("2d5710f1d420cf3c", "e321d462ff93287f", "9e0107edaf8d2a53"),
    ],
}
FLAGS = {"positive": (), "negative": (Flag.CLIPPING,)}


def _digest(array: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()[:16]


@pytest.fixture(scope="module")
def slides():
    return split_slides(make_sweep(dpi=600, dust=12))


@pytest.mark.parametrize("threads", [False, True], ids=["serial", "threaded"])
@pytest.mark.parametrize("name, options", [("positive", POSITIVE), ("negative", NEGATIVE)])
def test_outputs_are_pixel_identical_to_the_pinned_ones(
    slides, name, options, threads, monkeypatch
):
    if threads:  # force the threaded strip and inpaint paths, which 600 dpi slides never take
        monkeypatch.setattr(arrays, "STRIPS_PER_WORKER", 1)
        monkeypatch.setattr(arrays, "MAX_WORKERS", 4)
        monkeypatch.setattr(dust, "INPAINT_WORKERS", 4)
    else:
        monkeypatch.setattr(dust, "INPAINT_WORKERS", 1)
    for slide, (enhanced_hash, after_hash, before_hash) in zip(slides, PINNED[name], strict=True):
        enhanced = enhance_slide(slide, options, 600)
        assert _digest(enhanced) == enhanced_hash
        assert _digest(make_preview(enhanced)) == after_hash
        assert _digest(make_preview(slide.rgb, gamma=2.2)) == before_hash
        assert compute_flags(enhanced, slide.ir, 600) == FLAGS[name]
