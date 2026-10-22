"""Plain-words text for the window. Pure functions, so they are tested without Qt."""

from opticfilm_linux.batch.model import Flag, SlideStatus, TrayStage
from opticfilm_linux.scanner.base import ScannerStatus

_FLAGS = {
    Flag.TOO_DARK: "Very dark",
    Flag.TOO_BRIGHT: "Very bright",
    Flag.CLIPPING: "Highlights or shadows are cut off",
    Flag.DUST: "Dust or scratches detected",
}

_STAGES = {
    TrayStage.SCANNING: "Scanning",
    TrayStage.SCANNED: "Waiting to enhance",
    TrayStage.ENHANCING: "Enhancing",
    TrayStage.REVIEW: "Ready to review",
    TrayStage.EXPORTED: "Exported",
    TrayStage.FAILED: "Failed",
}

_SCANNER = {
    ScannerStatus.UNKNOWN: "Scanner status unknown: scanning is not available yet",
    ScannerStatus.READY: "Scanner ready",
    ScannerStatus.BUSY: "Scanner busy",
    ScannerStatus.NOT_READY: "Scanner not ready: load a tray",
    ScannerStatus.NOT_CONNECTED: "Scanner not connected",
    ScannerStatus.ERROR: "Scanner error",
}


def flag_text(flag: Flag) -> str:
    return _FLAGS[flag]


def stage_text(stage: TrayStage) -> str:
    return _STAGES[stage]


def scanner_text(status: ScannerStatus) -> str:
    return _SCANNER[status]


_SLIDE_STATUS = {
    SlideStatus.PENDING: "Pending",
    SlideStatus.NEEDS_LOOK: "Needs a look",
    SlideStatus.ACCEPTED: "Accepted",
    SlideStatus.BLANK: "Empty window",
}


def slide_status_text(status: SlideStatus) -> str:
    return _SLIDE_STATUS[status]


def bytes_text(size: int) -> str:
    """A size in plain words, e.g. ``1.3 GB``."""
    value = float(size)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1000 or unit == "GB":
            return f"{int(value)} bytes" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} GB"  # unreachable; keeps the type checker content
