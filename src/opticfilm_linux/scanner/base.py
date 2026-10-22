"""The scanner interface the rest of the program depends on."""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from opticfilm_linux.model import RawSweep, ScanSettings


class ScanError(RuntimeError):
    """A scan could not be carried out."""


class NotSupportedYet(ScanError):
    """The requested operation depends on driver support that does not exist yet."""


@dataclass(frozen=True)
class DeviceInfo:
    vendor_id: int
    product_id: int
    name: str
    bus: int
    address: int


@runtime_checkable
class Scanner(Protocol):
    def scan_holder(self, settings: ScanSettings) -> RawSweep: ...

    def eject(self) -> None: ...
