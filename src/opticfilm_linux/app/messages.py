"""Plain-words text for the window. Pure functions, so they are tested without Qt."""

import shlex
from pathlib import Path

from opticfilm_linux.batch.model import Flag, SlideStatus, TrayStage
from opticfilm_linux.scanner.base import ScannerStatus
from opticfilm_linux.scanner.driver import POWER_CYCLE_HINT

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
    ScannerStatus.NOT_READY: (
        'Scanner is not loaded. Open "How to load the scanner", then press Scan.'
    ),
    ScannerStatus.NOT_CONNECTED: "Scanner not found. Switch it on and check the USB cable.",
    ScannerStatus.ERROR: (
        "The scanner reports a problem. If a scan just failed or stopped, power-cycle it "
        "(off until the lights go out, then on), eject and load the holder again. If the app "
        'says the scanner driver was not found, see "How to load the scanner" / the README '
        "(OPTICFILM_DRIVER_DIR)."
    ),
    ScannerStatus.INTERRUPTED: (
        "A scan or holder move was cut off, so the scanner's state is unknown. Power-cycle it "
        "(off until the lights go out, then on), load the holder again, press Confirm scanner "
        "reset, then rescan the interrupted tray."
    ),
}

POWER_CYCLE_ADVICE = (
    "The scanner stopped this scan. Power-cycle it (off until the lights go out, then on), "
    "eject, load the holder again and rescan this tray."
)
DRIVER_DIR_PLACEHOLDER = "~/opticfilm135i-linux"


def flag_text(flag: Flag) -> str:
    return _FLAGS[flag]


def stage_text(stage: TrayStage) -> str:
    return _STAGES[stage]


def scanner_text(status: ScannerStatus) -> str:
    return _SCANNER[status]


def failure_text(detail: str, fallback: str) -> str:
    """The short sentence for a failure banner; the technical ``detail`` goes in its details."""
    return POWER_CYCLE_ADVICE if POWER_CYCLE_HINT in detail else fallback  # the backend's words


_HOLDER_ACTIONS = {"eject": "eject the holder", "load": "load the next holder"}


def holder_failed_text(action: str) -> str:
    """A failed holder move: the driver then refuses everything until a power cycle."""
    return (
        f"Could not {_HOLDER_ACTIONS[action]}. Switch the scanner off until its lights go out, "
        'switch it on, then follow "How to load the scanner".'
    )


def load_command(driver_dir: Path | None) -> str:
    """The terminal command for the first, interactive load after the scanner is switched on."""
    folder = shlex.quote(str(driver_dir)) if driver_dir is not None else DRIVER_DIR_PLACEHOLDER
    inner = f"cd -- {folder} && PYTHONPATH=. python3 -m of135i load"
    return f"distrobox enter dev -- bash -lc {shlex.quote(inner)}"


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
