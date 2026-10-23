"""Pins the pipeline's 600 dpi outputs byte for byte, so performance work cannot change pixels.

The hashes were taken before any optimisation (numpy 2.5, OpenCV 5.0 from the lockfile) on the
development machine. numpy and OpenCV pick SIMD code paths per CPU, so a different CPU (a CI
runner) can round float filters differently: the absolute pins are enforced only where a probe of
those library kernels, independent of this project's code, gives the development machine's
result. Everywhere, the current code (serial and threaded) is compared with frozen copies of the
pre-change implementations (tests/reference_pipeline.py) in the same environment. If a dependency
update changes the pins, check the images by eye before re-pinning.
"""

import hashlib

import cv2
import numpy as np
import pytest

import reference_pipeline
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
PROBE = "06cd634ee476ceeb"  # _kernel_probe() on the machine the pins were taken on


def _digest(array: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()[:16]


def _kernel_probe() -> str:
    """The library kernels the pipeline relies on, on a fixed input: no project code involved."""
    x = np.random.default_rng(7).random((64, 64, 3), dtype=np.float32)
    x8 = (x * 255).astype(np.uint8)
    x16 = (x * 65535).astype(np.uint16)
    parts = [
        cv2.GaussianBlur(x, (0, 0), 1.5),
        cv2.bilateralFilter(x, -1, 0.1, 2.5),
        np.clip((x - 0.1) / 0.8, 0, 1) ** np.float32(0.7),
        -np.log10(np.clip(x, 1e-4, 1.0)),  # negative inversion's density
        np.log(x.astype(np.float64)),
        cv2.medianBlur(x8[..., 0], 21),
        cv2.inpaint(x8, (x8[..., 0] > 240).astype(np.uint8) * 255, 3, cv2.INPAINT_TELEA),
        cv2.resize(x16, (20, 20), interpolation=cv2.INTER_AREA),
        np.percentile(x, [0.1, 50, 99.9]),
    ]
    return _digest(np.concatenate([np.ascontiguousarray(p).view(np.uint8).ravel() for p in parts]))


PINNED_ON_THIS_CPU = _kernel_probe() == PROBE


@pytest.fixture(scope="module")
def slides():
    return split_slides(make_sweep(dpi=600, dust=12))


def _outputs(slides, options, threads: bool, monkeypatch) -> list[tuple]:
    if threads:  # force the threaded strip and inpaint paths, which 600 dpi slides never take
        monkeypatch.setattr(arrays, "STRIPS_PER_WORKER", 1)
        monkeypatch.setattr(arrays, "MAX_WORKERS", 4)
        monkeypatch.setattr(dust, "INPAINT_WORKERS", 4)
    else:
        monkeypatch.setattr(arrays, "MAX_WORKERS", 1)
        monkeypatch.setattr(dust, "INPAINT_WORKERS", 1)
    results = []
    for slide in slides:
        enhanced = enhance_slide(slide, options, 600)
        digests = (_digest(enhanced), _digest(make_preview(enhanced)))
        before = _digest(make_preview(slide.rgb, gamma=2.2))
        results.append((*digests, before, compute_flags(enhanced, slide.ir, 600)))
    return results


MIRRORED = ProcessOptions(mirror=True, rotation=270, sharpen_amount=1.0, grain_strength=0.6)


@pytest.mark.parametrize(
    "name, options",
    [("positive", POSITIVE), ("negative", NEGATIVE), ("mirrored", MIRRORED)],
)
def test_outputs_match_the_frozen_pre_change_implementation(slides, name, options, monkeypatch):
    """Runs on every CPU: old serial code and new code, side by side in this environment."""
    with monkeypatch.context() as frozen:
        reference_pipeline.use_reference(frozen)
        reference = _outputs(slides, options, False, frozen)
    assert _outputs(slides, options, False, monkeypatch) == reference
    assert _outputs(slides, options, True, monkeypatch) == reference
    if name in FLAGS:
        assert [flags for *_, flags in reference] == [FLAGS[name]] * 4


@pytest.mark.skipif(not PINNED_ON_THIS_CPU, reason="library kernels round differently on this CPU")
@pytest.mark.parametrize("threads", [False, True], ids=["serial", "threaded"])
@pytest.mark.parametrize("name, options", [("positive", POSITIVE), ("negative", NEGATIVE)])
def test_outputs_are_pixel_identical_to_the_pinned_ones(
    slides, name, options, threads, monkeypatch
):
    expected = [(*hashes, FLAGS[name]) for hashes in PINNED[name]]
    assert _outputs(slides, options, threads, monkeypatch) == expected
