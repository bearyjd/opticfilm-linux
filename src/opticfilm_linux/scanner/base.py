"""The scanner interface the rest of the program depends on."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from opticfilm_linux.model import RawSweep, ScanSettings


class ScanError(RuntimeError):
    """A scan could not be carried out."""


class NotSupportedYet(ScanError):
    """The requested operation depends on driver support that does not exist yet."""


class ScannerNotReady(ScanError):
    """A scan was requested while the scanner was not ready."""


class ScannerStatus(StrEnum):
    NOT_CONNECTED = "not_connected"
    BUSY = "busy"
    NOT_READY = "not_ready"  # connected, but no tray loaded or not yet settled
    READY = "ready"
    ERROR = "error"
    UNKNOWN = "unknown"  # the backend cannot tell
    INTERRUPTED = "interrupted"  # a command was cut off: the owner must confirm a reset


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

    def status(self) -> ScannerStatus: ...

    def eject(self) -> None: ...


@runtime_checkable
class HolderControl(Protocol):
    """A scanner that can move the holder: eject it, and load the next one without a terminal.

    Separate from ``Scanner`` so a backend without it is still a scanner; the app then simply
    disables its holder buttons.
    """

    def eject(self) -> None: ...

    def load_next_strip(self) -> None: ...
