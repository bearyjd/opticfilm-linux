"""Immutable data types shared by the scanner layer and the pipeline."""

from dataclasses import dataclass
from enum import Enum
from typing import Literal

import numpy as np


class HolderKind(str, Enum):
    SLIDE4 = "slide4"
    STRIP6 = "strip6"


@dataclass(frozen=True)
class ScanSettings:
    dpi: int = 3600
    mode: Literal["positive", "negative"] = "positive"
    infrared: bool = True


@dataclass(frozen=True)
class Box:
    """A pixel rectangle; ``y`` runs along the sweep."""

    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class RawSweep:
    """One whole-holder sweep. ``rgb`` is (H, W, 3) uint16, ``ir`` is (H, W) uint16 or None."""

    rgb: np.ndarray
    ir: np.ndarray | None
    dpi: int
    holder: HolderKind

    def __post_init__(self) -> None:
        if self.rgb.dtype != np.uint16:
            raise ValueError(f"rgb must be uint16, got {self.rgb.dtype}")
        if self.rgb.ndim != 3 or self.rgb.shape[2] != 3:
            raise ValueError(f"rgb must have shape (H, W, 3), got {self.rgb.shape}")
        if self.ir is None:
            return
        if self.ir.dtype != np.uint16:
            raise ValueError(f"ir must be uint16, got {self.ir.dtype}")
        if self.ir.shape != self.rgb.shape[:2]:
            raise ValueError(
                f"ir shape {self.ir.shape} does not match rgb shape {self.rgb.shape[:2]}"
            )


@dataclass(frozen=True)
class Slide:
    index: int
    rgb: np.ndarray
    ir: np.ndarray | None
    box: Box
