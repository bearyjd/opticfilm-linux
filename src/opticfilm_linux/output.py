"""Write finished slides to disk with sequence numbers and an embedded sRGB profile."""

import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import cache
from math import floor
from pathlib import Path
from typing import BinaryIO, Literal

import numpy as np
import tifffile
from PIL import Image, ImageCms

from opticfilm_linux.provenance import XMP_HEADER, JpegMetadata

Format = Literal["tiff16", "png8", "jpeg"]
JpegSize = Literal["full", "3600"]
EXTENSIONS: dict[str, str] = {"tiff16": "tif", "png8": "png", "jpeg": "jpg"}
JPEG_QUALITY = 95


def jpeg_geometry(width: int, height: int, scan_dpi: int, size: JpegSize) -> tuple[int, int, int]:
    """Size the rendered frame using its sweep resolution, without upscaling."""
    if type(scan_dpi) is not int or scan_dpi <= 0:
        raise ValueError("scan_dpi must be a positive integer")
    if size not in ("full", "3600"):
        raise ValueError(f"unknown JPEG size {size!r}")
    divisor = max(1, scan_dpi / 3600) if size == "3600" else 1
    delivered_dpi = min(scan_dpi, 3600) if size == "3600" else scan_dpi
    return (
        max(1, floor(width / divisor + 0.5)),
        max(1, floor(height / divisor + 0.5)),
        delivered_dpi,
    )


@dataclass(frozen=True)
class OutputSpec:
    directory: Path
    fmt: Format = "tiff16"
    album: str = ""
    date: str | None = None
    scan_dpi: int | None = field(default=None, kw_only=True)
    jpeg_size: JpegSize = field(default="full", kw_only=True)
    metadata: JpegMetadata | None = field(default=None, kw_only=True)

    position: int | None = field(default=None, kw_only=True)
    sequence_stride: int = field(default=1, kw_only=True)

    def __post_init__(self) -> None:
        if self.position is not None and (
            type(self.position) is not int or not 1 <= self.position <= 999
        ):
            raise ValueError("position must be between 1 and 999")
        if type(self.sequence_stride) is not int or self.sequence_stride < 1:
            raise ValueError("sequence_stride must be a positive integer")
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


def _next_sequence(directory: int, prefix: str) -> int:
    pattern = re.compile(rf"^{re.escape(prefix)}(\d{{4}})\.(?:tif|png|jpg)$")
    used = [int(m.group(1)) for name in os.listdir(directory) if (m := pattern.match(name))]
    return max(used, default=0) + 1


@contextmanager
def _directory_fd(directory: Path, *, create: bool):
    """Anchor every directory component without following a replaceable symlink."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(os.path.sep, flags)
    try:
        for component in Path(os.path.abspath(directory)).parts[1:]:
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(component, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _check_directory_identity(directory: Path, descriptor: int) -> None:
    with _directory_fd(directory, create=False) as current:
        expected, actual = os.fstat(descriptor), os.fstat(current)
        if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
            raise ValueError("Export directory changed while writing; retry the export.")


def _same_file(parent: int, name: str, opened: os.stat_result) -> bool:
    try:
        entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return (entry.st_dev, entry.st_ino) == (opened.st_dev, opened.st_ino)


def _encode(
    rgb: np.ndarray,
    fmt: str,
    handle: BinaryIO,
    *,
    scan_dpi: int | None = None,
    jpeg_size: JpegSize = "full",
    metadata: JpegMetadata | None = None,
) -> None:
    if fmt == "tiff16":
        tifffile.imwrite(handle, rgb, photometric="rgb", iccprofile=_srgb_profile())
        return
    image = Image.fromarray(_to_8bit(rgb), "RGB")
    if fmt == "png8":
        image.save(handle, format="PNG", icc_profile=_srgb_profile())
    else:
        exif = Image.Exif()
        if metadata and metadata.exif:
            exif.load(metadata.exif)
        if scan_dpi is not None:
            width, height, delivered_dpi = jpeg_geometry(*image.size, scan_dpi, jpeg_size)
            if image.size != (width, height):
                image = image.resize((width, height), Image.Resampling.LANCZOS)
            exif[282] = exif[283] = delivered_dpi
            exif[296] = 2
        target = handle
        if metadata:
            payload = XMP_HEADER + metadata.xmp
            if len(payload) > 65533:
                raise ValueError("XMP APP1 payload exceeds 65533 bytes")
            packet = b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
            target = _XmpWriter(handle, packet)
        image.save(
            target, format="JPEG", quality=JPEG_QUALITY, icc_profile=_srgb_profile(), exif=exif
        )


class _XmpWriter:
    """Insert APP1 at JPEG SOI while Pillow streams to the exclusively opened file."""

    def __init__(self, handle: BinaryIO, packet: bytes):
        self.handle = handle
        self.packet = packet
        self.started = False

    def write(self, data: bytes) -> int:
        if not self.started:
            if not data.startswith(b"\xff\xd8"):
                raise ValueError("JPEG encoder did not write SOI")
            self.handle.write(data[:2])
            self.handle.write(self.packet)
            self.handle.write(data[2:])
            self.started = True
        else:
            self.handle.write(data)
        return len(data)

    def flush(self) -> None:
        self.handle.flush()


def write_slide(rgb: np.ndarray, spec: OutputSpec) -> Path:
    """Write ``rgb`` under the next free sequence number; never overwrites an existing file."""
    extension = EXTENSIONS.get(spec.fmt)
    if extension is None:
        raise ValueError(f"unknown output format {spec.fmt!r}; use one of {sorted(EXTENSIONS)}")
    if spec.fmt == "jpeg" and spec.scan_dpi is not None:
        jpeg_geometry(rgb.shape[1], rgb.shape[0], spec.scan_dpi, spec.jpeg_size)
    prefix = _prefix(spec) if spec.position is None else ""
    with _directory_fd(spec.directory, create=True) as parent:
        sequence = _next_sequence(parent, prefix) if spec.position is None else spec.position
        while True:
            if spec.position is not None and sequence > 999:
                raise ValueError(
                    "No free position filename remains below 999; choose a different export folder."
                )
            width = 4 if spec.position is None else 3
            name = f"{prefix}{sequence:0{width}d}.{extension}"
            path = spec.directory / name
            try:
                descriptor = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o666,
                    dir_fd=parent,
                )
            except FileExistsError:
                sequence += spec.sequence_stride
                continue
            opened = os.fstat(descriptor)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    # tifffile reads a stream's name for its label; it never reopens it.
                    handle.raw.name = str(path)
                    _encode(
                        rgb,
                        spec.fmt,
                        handle,
                        scan_dpi=spec.scan_dpi,
                        jpeg_size=spec.jpeg_size,
                        metadata=spec.metadata,
                    )
                _check_directory_identity(spec.directory, parent)
                if not _same_file(parent, name, opened):
                    raise ValueError("Export file changed while writing; retry the export.")
            except BaseException:
                if _same_file(parent, name, opened):
                    os.unlink(name, dir_fd=parent)
                raise
            return path
