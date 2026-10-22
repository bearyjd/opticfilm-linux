"""Placeholder for the real scanner backend built on the community driver.

The community driver (opticfilm135i-linux) has no mounted-slide sweep yet, so nothing here
talks to the scanner. This class exists so the rest of the program has one honest place that
says why real scanning is unavailable.
"""

from opticfilm_linux.model import RawSweep, ScanSettings
from opticfilm_linux.scanner.base import DeviceInfo, NotSupportedYet, ScannerStatus

REASON = (
    "scanning a four-slide holder is not supported yet: it depends on slide-holder support in "
    "the upstream opticfilm135i-linux driver (its roadmap item C2)"
)


class CommunityDriverScanner:
    def __init__(self, device: DeviceInfo) -> None:
        self._device = device

    def scan_holder(self, settings: ScanSettings) -> RawSweep:
        raise NotSupportedYet(REASON)

    def status(self) -> ScannerStatus:
        return ScannerStatus.UNKNOWN

    def eject(self) -> None:
        raise NotSupportedYet(REASON)
