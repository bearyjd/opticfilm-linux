"""Plain-words text for the window. Pure functions, so they are tested without Qt."""

import logging
import os
import re
import shlex
import shutil
from collections.abc import Callable
from pathlib import Path

from opticfilm_linux.batch.model import Flag, SlideStatus, TrayStage
from opticfilm_linux.scanner.base import ScannerStatus
from opticfilm_linux.scanner.driver import POWER_CYCLE_HINT
from opticfilm_linux.scanner.holder_latch import HolderLatch

log = logging.getLogger(__name__)

RUNNER_ENV = "OPTICFILM_DRIVER_RUNNER"  # unset = distrobox "dev"; "host"; "distrobox:<name>"
DEFAULT_CONTAINER = "dev"
_CONTAINER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")  # a leading - would read as an option

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


def holder_latch_text(latch: HolderLatch) -> str:
    return {
        HolderLatch.LATCHED: "Holder: latched",
        HolderLatch.RELEASED: "Holder: released",
        HolderLatch.UNKNOWN: (
            "Holder: unknown after power-on or a fault; check by hand before scanning"
        ),
    }[latch]


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


_SHOWN_VALUE_MAX = 40


def _parse_runner(runner: str | None) -> tuple[str | None, str]:
    """The container (``None`` = host) and the rejected value ("" if it was understood).

    Bad values fall back to the default container.
    """
    value = (os.environ.get(RUNNER_ENV, "") if runner is None else runner).strip()
    if value == "host":
        return None, ""
    name = value.removeprefix("distrobox:") if value.startswith("distrobox:") else None
    if name is not None and _CONTAINER_NAME.fullmatch(name):
        return name, ""
    return DEFAULT_CONTAINER, value


def load_command(driver_dir: Path | None, runner: str | None = None) -> str:
    """The terminal command for the first, interactive load after the scanner is switched on.

    ``runner`` defaults to ``OPTICFILM_DRIVER_RUNNER``: unset runs the driver in the distrobox
    container ``dev``, ``host`` runs it directly, ``distrobox:<name>`` names the container.
    A value that is not understood is logged here, once, and the default is used.
    """
    folder = shlex.quote(str(driver_dir)) if driver_dir is not None else DRIVER_DIR_PLACEHOLDER
    inner = f"cd -- {folder} && PYTHONPATH=. python3 -m of135i load"
    container, rejected = _parse_runner(runner)
    if rejected:
        log.warning(
            "%s=%r is not 'host' or 'distrobox:<name>'; using distrobox %r",
            RUNNER_ENV,
            rejected,
            DEFAULT_CONTAINER,
        )
    # The name pattern already makes quoting a no-op; kept as defence in depth against a shell.
    prefix = "" if container is None else f"distrobox enter {shlex.quote(container)} -- "
    return f"{prefix}bash -lc {shlex.quote(inner)}"


def _shown(value: str) -> str:
    """A user-supplied value made safe to put in a sentence: printable only, and short."""
    text = "".join(ch if ch.isprintable() else "?" for ch in value)
    return text if len(text) <= _SHOWN_VALUE_MAX else text[:_SHOWN_VALUE_MAX] + "..."


def runner_note(
    runner: str | None = None, which: Callable[[str], str | None] = shutil.which
) -> str:
    """The help text's sentence on which runner the command assumes and how to change it."""
    container, rejected = _parse_runner(runner)
    if container is None:
        return (
            "This command runs the driver directly on this computer. "
            f"To run it in a distrobox container instead, set {RUNNER_ENV}=distrobox:<name>."
        )
    note = ""
    if rejected:
        note = f'{RUNNER_ENV}="{_shown(rejected)}" was not understood; using "{container}". '
    note += (
        f'This command runs the driver inside the distrobox container "{container}". '
        f"To run it directly on this computer instead, set {RUNNER_ENV}=host "
        f"(or {RUNNER_ENV}=distrobox:<name> for another container)."
    )
    if which("distrobox") is None:
        note += (
            " The distrobox command was not found on this computer, so this command will not "
            f"work here: install distrobox, or set {RUNNER_ENV}=host to run the driver directly."
        )
    return note


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
