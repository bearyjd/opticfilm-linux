"""Read-only detection of supported scanners from the kernel's USB listing in sysfs."""

from pathlib import Path

from opticfilm_linux.scanner.base import DeviceInfo

SYSFS_USB_DEVICES = Path("/sys/bus/usb/devices")
KNOWN_DEVICES: dict[tuple[int, int], str] = {(0x07B3, 0x1436): "Plustek OpticFilm 135i"}


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _device_info(directory: Path) -> DeviceInfo | None:
    try:
        vendor = int(_read(directory / "idVendor") or "", 16)
        product = int(_read(directory / "idProduct") or "", 16)
        bus = int(_read(directory / "busnum") or "")
        address = int(_read(directory / "devnum") or "")
    except ValueError:
        return None
    name = KNOWN_DEVICES.get((vendor, product))
    if name is None:
        return None
    return DeviceInfo(vendor_id=vendor, product_id=product, name=name, bus=bus, address=address)


def find_devices(sysfs_root: Path = SYSFS_USB_DEVICES) -> list[DeviceInfo]:
    """List supported scanners. Only reads sysfs; never opens or writes to a device."""
    try:
        entries = sorted(sysfs_root.iterdir())
    except OSError:
        return []
    return [info for entry in entries if (info := _device_info(entry)) is not None]
