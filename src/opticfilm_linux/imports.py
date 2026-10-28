"""Enhance finished images from other scanning software (for example the vendor's own app).

Each image is one slide. There is no infrared plane, so dust removal is off; everything else
(exposure, colour, grain, sharpening, numbered never-overwritten output) is the normal pipeline,
except that the pixels are already tone mapped: they take the non-linear exposure path.
"""

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image, ImageOps

from opticfilm_linux.model import Box, Slide
from opticfilm_linux.output import OutputSpec, write_slide
from opticfilm_linux.pipeline.run import ProcessOptions, RunResult, enhance_slide

TIFF_SUFFIXES = (".tif", ".tiff")
MAX_PIXELS = 400_000_000  # about a 7200 dpi slide with plenty of room
FULL_16 = 65535
EIGHT_TO_SIXTEEN = 257  # 255 * 257 == 65535


def load_image(path: Path) -> np.ndarray:
    """An RGB uint16 array from a TIFF, PNG or JPEG; 8-bit input is scaled to the full range."""
    try:
        data = _read(path)
    except (OSError, ValueError, tifffile.TiffFileError, Image.DecompressionBombError) as exc:
        raise ValueError(f"cannot read {path.name}: {exc}") from exc
    return _as_rgb16(data, path)


def _read(path: Path) -> np.ndarray:
    if path.suffix.lower() in TIFF_SUFFIXES:
        return _read_tiff(path)
    with Image.open(path) as image:
        _check_size(image.width * image.height)
        image = ImageOps.exif_transpose(image)  # other software often records a turn, not pixels
        if image.mode not in ("I;16", "I;16L", "I;16B"):
            image = image.convert("RGB")
        return np.asarray(image)


def _read_tiff(path: Path) -> np.ndarray:
    with tifffile.TiffFile(path) as handle:
        page = handle.pages[0]
        _check_size(int(np.prod(page.shape[:2])))
        if page.photometric.name not in ("RGB", "MINISBLACK"):
            raise ValueError(f"unsupported TIFF colour model {page.photometric.name}")
        return np.asarray(page.asarray())


def _check_size(pixels: int) -> None:
    if pixels > MAX_PIXELS:
        raise ValueError(f"image is too large ({pixels} pixels)")


def _as_rgb16(data: np.ndarray, path: Path) -> np.ndarray:
    if data.ndim == 2:
        data = data[:, :, None]
    if data.ndim != 3 or data.shape[2] not in (1, 3, 4):
        raise ValueError(f"cannot read {path.name}: unsupported shape {data.shape}")
    data = data[:, :, :3]
    if data.shape[2] == 1:
        data = np.repeat(data, 3, axis=2)
    if data.dtype == np.uint8:
        return data.astype(np.uint16) * EIGHT_TO_SIXTEEN
    if data.dtype == np.uint16:
        return np.ascontiguousarray(data)
    raise ValueError(f"cannot read {path.name}: unsupported pixel type {data.dtype}")


def process_images(
    paths: Sequence[Path], options: ProcessOptions, out: OutputSpec, dpi: int
) -> RunResult:
    written: list[Path] = []
    for index, path in enumerate(paths):
        rgb = load_image(path)
        slide = Slide(
            index=index, rgb=rgb, ir=None, box=Box(x=0, y=0, w=rgb.shape[1], h=rgb.shape[0])
        )
        written.append(write_slide(enhance_slide(slide, options, dpi, linear=False), out))
    return RunResult(written=tuple(written), blank_positions=())
