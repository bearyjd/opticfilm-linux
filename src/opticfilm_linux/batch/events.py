"""What the runner reports while it works. Events are frozen and safe to read from any thread."""

from dataclasses import dataclass

from opticfilm_linux.batch.model import TrayStage


@dataclass(frozen=True)
class TrayStageChanged:
    number: int
    stage: TrayStage


@dataclass(frozen=True)
class SlideUpdated:
    number: int
    position: int


@dataclass(frozen=True)
class TrayFailed:
    number: int
    message: str
    operation_sequence: int = 0


@dataclass(frozen=True)
class ScanFinished:
    """A scan job has completely ended and no longer holds the runner (``scan_pending()`` is
    already False). ``ok``: it returned normally, saved its sweep and moved the tray to SCANNED,
    with no stop requested; any failure, stop or Ctrl-C gives False. ``driver_exit``: that
    scan's own driver exit code (0 when the scanner reports none), read before any later scan."""

    number: int
    ok: bool
    driver_exit: int = 0


BatchEvent = TrayStageChanged | SlideUpdated | TrayFailed | ScanFinished
