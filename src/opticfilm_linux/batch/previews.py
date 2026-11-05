"""Small 8-bit JPEG previews so a UI never has to read the huge raw or 16-bit files."""

import os
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, PngImagePlugin

FULL_SCALE = 65535.0
JPEG_QUALITY = 90
MASTER_ORIENTATION = "orientation"  # PNG text chunk: "<rotation>,<0|1 mirror>"


def make_preview(rgb: np.ndarray, gamma: float = 1.0, long_side: int = 1600) -> np.ndarray:
    """Downscale (never upscale) a uint16 RGB image to 8 bits, applying display gamma."""
    height, width = rgb.shape[:2]
    scale = long_side / max(height, width)
    if scale < 1.0:
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        rgb = cv2.resize(rgb, size, interpolation=cv2.INTER_AREA)
    values = rgb.astype(np.float32) / FULL_SCALE
    if gamma != 1.0:
        values = values ** (1.0 / gamma)
    return np.clip(np.rint(values * 255.0), 0, 255).astype(np.uint8)


def write_preview(rgb8: np.ndarray, path: Path) -> None:
    """Write a JPEG; the file appears whole or not at all, so a UI never reads a half file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".tmp.jpg")
    try:
        Image.fromarray(rgb8, "RGB").save(tmp, format="JPEG", quality=JPEG_QUALITY)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_master(rgb8: np.ndarray, orientation: tuple[int, bool], path: Path) -> None:
    """Write the lossless twin of an "after" preview, tagged with the orientation it shows.

    The tag lives inside the PNG, so pixels and orientation are replaced together by one rename.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".tmp.png")
    info = PngImagePlugin.PngInfo()
    info.add_text(MASTER_ORIENTATION, f"{orientation[0]},{int(orientation[1])}")
    try:
        Image.fromarray(rgb8, "RGB").save(tmp, format="PNG", pnginfo=info, compress_level=1)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def read_master(path: Path) -> tuple[np.ndarray, tuple[int, bool]] | None:
    """The master's pixels and orientation, or None when it is missing or not readable."""
    try:
        with Image.open(path) as image:
            rotation, mirror = image.text[MASTER_ORIENTATION].split(",")
            pixels = np.asarray(image.convert("RGB"))
        return pixels, (int(rotation), mirror == "1")
    except (OSError, KeyError, ValueError, AttributeError):
        return None
