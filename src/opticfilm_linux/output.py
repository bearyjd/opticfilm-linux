"""Write finished slides to disk with sequence numbers and an embedded sRGB profile."""

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import BinaryIO, Literal

import numpy as np
import tifffile
from PIL import Image, ImageCms

Format = Literal["tiff16", "png8", "jpeg"]
EXTENSIONS: dict[str, str] = {"tiff16": "tif", "png8": "png", "jpeg": "jpg"}
JPEG_QUALITY = 95


@dataclass(frozen=True)
class OutputSpec:
    directory: Path
    fmt: Format = "tiff16"
    album: str = ""
    date: str | None = None

    def __post_init__(self) -> None:
        for label, value in (("album", self.album), ("date", self.date or "")):
            if "/" in value or "\\" in value or value in (".", ".."):
                raise ValueError(f"{label} must not contain path separators or be '..': {value!r}")


@cache
def _srgb_profile() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _to_8bit(rgb: np.ndarray) -> np.ndarray:
    return ((rgb.astype(np.uint32) + 128) // 257).astype(np.uint8)


def _prefix(spec: OutputSpec) -> str:
    parts = [part for part in (spec.album, spec.date) if part]
    return "_".join(parts) + "_" if parts else ""


def _next_sequence(directory: Path, prefix: str) -> int:
    pattern = re.compile(rf"^{re.escape(prefix)}(\d{{4}})\.(?:tif|png|jpg)$")
    used = [int(m.group(1)) for p in directory.iterdir() if (m := pattern.match(p.name))]
    return max(used, default=0) + 1


def _encode(rgb: np.ndarray, fmt: str, handle: BinaryIO) -> None:
    if fmt == "tiff16":
        tifffile.imwrite(handle, rgb, photometric="rgb", iccprofile=_srgb_profile())
        return
    image = Image.fromarray(_to_8bit(rgb), "RGB")
    if fmt == "png8":
        image.save(handle, format="PNG", icc_profile=_srgb_profile())
    else:
        image.save(handle, format="JPEG", quality=JPEG_QUALITY, icc_profile=_srgb_profile())


def write_slide(rgb: np.ndarray, spec: OutputSpec) -> Path:
    """Write ``rgb`` under the next free sequence number; never overwrites an existing file."""
    extension = EXTENSIONS.get(spec.fmt)
    if extension is None:
        raise ValueError(f"unknown output format {spec.fmt!r}; use one of {sorted(EXTENSIONS)}")
    spec.directory.mkdir(parents=True, exist_ok=True)
    prefix = _prefix(spec)
    sequence = _next_sequence(spec.directory, prefix)
    while True:
        path = spec.directory / f"{prefix}{sequence:04d}.{extension}"
        try:
            handle = open(path, "xb")  # noqa: SIM115 - exclusive create, closed below
        except FileExistsError:
            sequence += 1
            continue
        try:
            with handle:
                _encode(rgb, spec.fmt, handle)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path
