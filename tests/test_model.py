import numpy as np
import pytest

from opticfilm_linux.model import Box, HolderKind, RawSweep, ScanSettings, Slide


def _rgb(h: int = 8, w: int = 6) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint16)


def test_scan_settings_defaults():
    settings = ScanSettings()
    assert (settings.dpi, settings.mode, settings.infrared) == (3600, "positive", True)


def test_holder_kind_values():
    assert HolderKind.SLIDE4.value == "slide4"
    assert HolderKind.STRIP6.value == "strip6"


def test_valid_sweep_is_accepted():
    sweep = RawSweep(rgb=_rgb(), ir=np.zeros((8, 6), np.uint16), dpi=600, holder=HolderKind.SLIDE4)
    assert sweep.rgb.shape == (8, 6, 3)


def test_rawsweep_rejects_wrong_dtype():
    with pytest.raises(ValueError, match="uint16"):
        RawSweep(rgb=_rgb().astype(np.float32), ir=None, dpi=600, holder=HolderKind.SLIDE4)


def test_rawsweep_rejects_wrong_rgb_shape():
    with pytest.raises(ValueError, match="shape"):
        RawSweep(rgb=np.zeros((8, 6), np.uint16), ir=None, dpi=600, holder=HolderKind.SLIDE4)


def test_rawsweep_rejects_mismatched_ir_shape():
    with pytest.raises(ValueError, match="ir"):
        RawSweep(rgb=_rgb(), ir=np.zeros((9, 6), np.uint16), dpi=600, holder=HolderKind.SLIDE4)


def test_slide_and_box_are_frozen():
    box = Box(x=0, y=1, w=2, h=3)
    slide = Slide(index=0, rgb=_rgb(), ir=None, box=box)
    with pytest.raises(AttributeError):
        box.x = 5  # type: ignore[misc]
    with pytest.raises(AttributeError):
        slide.index = 1  # type: ignore[misc]
