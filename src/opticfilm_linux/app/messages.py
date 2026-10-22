"""Plain-words text for the window. Pure functions, so they are tested without Qt."""

from opticfilm_linux.batch.model import Flag, TrayStage
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
