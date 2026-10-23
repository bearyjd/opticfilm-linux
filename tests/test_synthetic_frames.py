"""The fake driver's synthetic full-window frames, and stitching them at awkward sizes."""

import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

from opticfilm_linux.model import ScanSettings
from opticfilm_linux.pipeline.split import split_slides
from opticfilm_linux.scanner.driver import DriverConfig, DriverScanner

FAKE_DRIVER = Path(__file__).parent / "fake_driver"
sys.path.insert(0, str(FAKE_DRIVER))
from of135i import synth  # noqa: E402


def test_the_frame_geometry_follows_the_real_driver():
    assert (synth.FrameGeometry.at(600).lines, synth.FrameGeometry.at(600).pixels) == (927, 876)
    assert (synth.FrameGeometry.at(3600).lines, synth.FrameGeometry.at(3600).pixels) == (5562, 5256)
    big = synth.FrameGeometry.at(7200)
    assert (big.lines, big.pixels) == (11124, 10512)


def test_frames_are_deterministic_and_the_ir_has_three_equal_channels(tmp_path):
    synth.write_frame(tmp_path / "a.tiff", 300, 2, infrared=False)
    synth.write_frame(tmp_path / "b.tiff", 300, 2, infrared=False)
    synth.write_frame(tmp_path / "ir.tiff", 300, 2, infrared=True)
    a, b = tifffile.imread(tmp_path / "a.tiff"), tifffile.imread(tmp_path / "b.tiff")
    ir = tifffile.imread(tmp_path / "ir.tiff")
    assert a.shape == (464, 438, 3) and a.dtype == np.uint16 and np.array_equal(a, b)
    assert np.array_equal(ir[..., 0], ir[..., 1]) and np.array_equal(ir[..., 0], ir[..., 2])
    g = synth.FrameGeometry.at(300)
    opening = ir[g.top : g.top + g.along, g.left : g.left + g.across, 0]
    assert opening.min() < 0.5 * opening.mean()  # the dust specks show in infrared


@pytest.mark.parametrize("dpi", [600, 601])  # 601 dpi: odd frame sizes (929 x 877)
def test_the_driver_scans_synthetic_frames_into_a_splittable_sweep(dpi, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DRIVER_SYNTH", "1")
    config = DriverConfig(driver_dir=FAKE_DRIVER, log_dir=tmp_path / "logs", work_dir=tmp_path)
    sweep = DriverScanner(config).scan_holder(ScanSettings(dpi=dpi))
    g = synth.FrameGeometry.at(dpi)
    assert sweep.rgb.shape == (4 * g.pixels, g.lines, 3) and sweep.ir.shape == sweep.rgb.shape[:2]
    slides = split_slides(sweep)
    assert [s.box.h for s in slides] == [g.across] * 4
    assert [s.box.y for s in slides] == [
        k * g.pixels + (g.pixels - g.left - g.across) for k in range(4)
    ]
