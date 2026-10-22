import numpy as np
import pytest

from opticfilm_linux.model import HolderKind, ScanSettings
from opticfilm_linux.scanner.base import NotSupportedYet, ScanError, Scanner
from opticfilm_linux.scanner.fake import FakeScanner
from opticfilm_linux.sweepstore import save_sweep
from synthetic import dust_centers, make_sweep, truth_boxes


def test_make_sweep_shape_and_dtype():
    sweep = make_sweep(dpi=600)
    assert sweep.rgb.shape == (round(226 / 25.4 * 600), round(35 / 25.4 * 600), 3)
    assert sweep.rgb.dtype == np.uint16
    assert sweep.ir is not None and sweep.ir.dtype == np.uint16
    assert sweep.holder is HolderKind.SLIDE4


def test_make_sweep_is_deterministic():
    a, b = make_sweep(dpi=600, dust=5, seed=3), make_sweep(dpi=600, dust=5, seed=3)
    assert np.array_equal(a.rgb, b.rgb) and np.array_equal(a.ir, b.ir)


def test_make_sweep_without_ir_has_no_ir_plane():
    assert make_sweep(dpi=600, ir=False).ir is None


def test_truth_boxes_geometry_at_600dpi():
    boxes = truth_boxes(600)
    assert len(boxes) == 4
    assert boxes[0].h == round(22.75 / 25.4 * 600)
    assert boxes[1].y - boxes[0].y == pytest.approx(62.6 / 25.4 * 600, abs=1)
    assert all(b.x == 0 and b.w == round(35 / 25.4 * 600) for b in boxes)


def test_apertures_are_bright_in_ir_and_mount_is_dark():
    sweep = make_sweep(dpi=600)
    box = truth_boxes(600)[0]
    inside = sweep.ir[box.y + 5 : box.y + box.h - 5].mean()
    mount = sweep.ir[: box.y - 5].mean()
    assert inside > 0.7 * 65535 and mount < 0.1 * 65535


def test_dust_zero_is_the_clean_twin_of_a_dusty_sweep():
    clean, dusty = make_sweep(dpi=600, dust=0, seed=2), make_sweep(dpi=600, dust=12, seed=2)
    changed = np.any(clean.rgb != dusty.rgb, axis=2)
    assert 0 < changed.mean() < 0.01
    assert len(dust_centers(600, dust=12, seed=2)) == 12


def test_fake_scanner_returns_stored_sweep(tmp_path):
    original = make_sweep(dpi=600)
    save_sweep(original, tmp_path)
    got = FakeScanner(tmp_path).scan_holder(ScanSettings(dpi=600))
    assert np.array_equal(got.rgb, original.rgb)


def test_fake_scanner_rejects_dpi_mismatch(tmp_path):
    save_sweep(make_sweep(dpi=600), tmp_path)
    with pytest.raises(ScanError, match="600"):
        FakeScanner(tmp_path).scan_holder(ScanSettings(dpi=3600))


def test_fake_scanner_satisfies_protocol(tmp_path):
    save_sweep(make_sweep(dpi=600), tmp_path)
    scanner = FakeScanner(tmp_path)
    assert isinstance(scanner, Scanner)
    assert scanner.eject() is None


def test_not_supported_yet_is_a_scan_error():
    assert issubclass(NotSupportedYet, ScanError)
