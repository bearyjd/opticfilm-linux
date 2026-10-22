"""Run the whole pipeline on one sweep: split, enhance each slide, write the files."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

from opticfilm_linux.model import RawSweep, Slide
from opticfilm_linux.output import OutputSpec, write_slide
from opticfilm_linux.pipeline.detail import reduce_grain, sharpen
from opticfilm_linux.pipeline.dust import remove_dust
from opticfilm_linux.pipeline.split import is_blank, split_slides
from opticfilm_linux.pipeline.tone import auto_exposure, invert_negative, restore_color


@dataclass(frozen=True)
class ProcessOptions:
    mode: Literal["positive", "negative"] = "positive"
    remove_dust: bool = True
    restore_color: bool = True
    sharpen_amount: float = 0.5
    grain_strength: float = 0.3
    skip_blank: bool = True


@dataclass(frozen=True)
class RunResult:
    written: tuple[Path, ...]
    blank_positions: tuple[int, ...]  # 1-based holder positions skipped as blank

    @property
    def blank_skipped(self) -> int:
        return len(self.blank_positions)


def enhance_slide(slide: Slide, options: ProcessOptions, dpi: int) -> np.ndarray:
    """Dust removal, negative inversion, exposure, colour, grain, sharpening, in that order."""
    image = slide.rgb
    if options.remove_dust:
        image = remove_dust(image, slide.ir, dpi=dpi)
    if options.mode == "negative":
        image = invert_negative(image)
    image = auto_exposure(image)
    if options.restore_color:
        image = restore_color(image)
    image = reduce_grain(image, strength=options.grain_strength)
    return sharpen(image, amount=options.sharpen_amount)


def process_sweep(sweep: RawSweep, options: ProcessOptions, out: OutputSpec) -> RunResult:
    written: list[Path] = []
    blank: list[int] = []
    for slide in split_slides(sweep):
        if options.skip_blank and is_blank(slide):
            blank.append(slide.index + 1)
            continue
        written.append(write_slide(enhance_slide(slide, options, sweep.dpi), out))
    return RunResult(written=tuple(written), blank_positions=tuple(blank))
