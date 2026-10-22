import pytest

from opticfilm_linux.model import ScanSettings
from opticfilm_linux.scanner.base import DeviceInfo, NotSupportedYet
from opticfilm_linux.scanner.community import CommunityDriverScanner
from opticfilm_linux.scanner.usb import KNOWN_DEVICES, find_devices


def _device(root, name, vendor, product, bus, address, product_name=None):
    directory = root / name
    directory.mkdir()
    (directory / "idVendor").write_text(f"{vendor}\n")
    (directory / "idProduct").write_text(f"{product}\n")
    (directory / "busnum").write_text(f"{bus}\n")
    (directory / "devnum").write_text(f"{address}\n")
    if product_name:
        (directory / "product").write_text(f"{product_name}\n")


def test_finds_known_scanner_in_fake_sysfs(tmp_path):
    _device(tmp_path, "3-1", "07b3", "1436", 3, 54, "Film Scanner(A81)")
    _device(tmp_path, "3-3", "18d1", "4ee7", 3, 53, "Phone")
    _device(tmp_path, "usb3", "1d6b", "0002", 3, 1, "xHCI Host Controller")
    assert find_devices(tmp_path) == [
        DeviceInfo(
            vendor_id=0x07B3, product_id=0x1436, name="Plustek OpticFilm 135i", bus=3, address=54
        )
    ]


def test_known_devices_table_has_the_135i():
    assert KNOWN_DEVICES[(0x07B3, 0x1436)] == "Plustek OpticFilm 135i"


def test_ignores_entries_missing_files(tmp_path):
    (tmp_path / "3-9").mkdir()
    (tmp_path / "3-9" / "idVendor").write_text("07b3\n")
    assert find_devices(tmp_path) == []


def test_ignores_unparseable_ids(tmp_path):
    _device(tmp_path, "3-1", "zzzz", "1436", 3, 54)
    assert find_devices(tmp_path) == []


def test_empty_or_missing_root_returns_empty_list(tmp_path):
    assert find_devices(tmp_path) == []
    assert find_devices(tmp_path / "does-not-exist") == []


def test_community_scanner_raises_not_supported_yet():
    device = DeviceInfo(0x07B3, 0x1436, "Plustek OpticFilm 135i", 3, 54)
    scanner = CommunityDriverScanner(device)
    with pytest.raises(NotSupportedYet, match="C2"):
        scanner.scan_holder(ScanSettings())
    with pytest.raises(NotSupportedYet, match="C2"):
        scanner.eject()


def test_community_scanner_status_is_unknown():
    from opticfilm_linux.scanner.base import ScannerStatus

    device = DeviceInfo(0x07B3, 0x1436, "Plustek OpticFilm 135i", 3, 54)
    assert CommunityDriverScanner(device).status() is ScannerStatus.UNKNOWN
