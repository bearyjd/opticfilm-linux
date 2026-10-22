import pytest

from opticfilm_linux.scanner.usb import find_devices


@pytest.mark.hardware
def test_real_scanner_is_visible():
    """Read-only: needs the OpticFilm 135i plugged in and powered on."""
    devices = find_devices()
    assert any(device.product_id == 0x1436 for device in devices)
