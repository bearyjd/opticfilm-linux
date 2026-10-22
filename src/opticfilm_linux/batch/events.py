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


BatchEvent = TrayStageChanged | SlideUpdated | TrayFailed
