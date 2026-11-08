"""Remembered preferences and the recent-batches list, behind a tiny storage interface.

The Qt layer backs ``Prefs`` with ``QSettings``; tests use ``MemoryPrefs``.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from opticfilm_linux.output import Format, JpegSize

DPI_CHOICES = (600, 1200, 2400, 3600, 7200)
DEFAULT_DPI = 3600  # owner decision 2026-11-07: never below 3600; 7200 is too slow for a big job
# Keep the raw scans after export. ONE switch for new batches: set False to let the app free each
# tray's raw scans after its export is verified (that path has not run on hardware yet).
DEFAULT_KEEP_RAW = True
MAX_RECENT = 10
RECENT_KEY = "recent_batches"
FALLBACK_NAME = "batch"
THEME_KEY = "theme"
DEFAULT_THEME = "dark"
THEMES = ("dark", "light")
EXPORT_FOLDER_KEY = "export_folder"
EXPORT_FORMAT_KEY = "export_format"
EXPORT_JPEG_SIZE_KEY = "export_jpeg_size"
AUTO_EJECT_KEY = "workflow/auto_eject"
SKIP_EMPTY_KEY = "workflow/skip_empty"
AUTO_EXPORT_KEY = "workflow/auto_export_default"
LOAD_AND_SCAN_KEY = "workflow/load_and_scan"
SWITCH_KEYS = (AUTO_EJECT_KEY, SKIP_EMPTY_KEY, AUTO_EXPORT_KEY, LOAD_AND_SCAN_KEY)
_SWITCH_ON = ("1", "true")
# Starting value of a switch that was never saved. A saved "0" or "1" always wins over this.
SWITCH_DEFAULTS = {AUTO_EXPORT_KEY: True, SKIP_EMPTY_KEY: True}


class Prefs(Protocol):
    def get(self, key: str, default: str | None = None) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...


class MemoryPrefs:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._values.get(key, default)

    def set(self, key: str, value: str) -> None:
        self._values[key] = value


def read_theme(prefs: Prefs) -> str:
    """Old settings and unrecognized values start in the application's dark theme."""
    value = prefs.get(THEME_KEY, DEFAULT_THEME)
    return value if value in THEMES else DEFAULT_THEME


def remember_theme(prefs: Prefs, name: str) -> None:
    if name not in THEMES:
        raise ValueError(f"Unknown theme: {name}")
    prefs.set(THEME_KEY, name)


def read_switch(prefs: Prefs, key: str) -> bool:
    """A saved workflow switch; never saved: ``SWITCH_DEFAULTS`` (off if not listed there).

    A saved value that is malformed is off."""
    if key not in SWITCH_KEYS:
        return False
    saved = (prefs.get(key) or "").strip().lower()
    if not saved:
        return SWITCH_DEFAULTS.get(key, False)
    return saved in _SWITCH_ON


def remember_switch(prefs: Prefs, key: str, value: bool) -> None:
    if key not in SWITCH_KEYS:
        raise ValueError(f"Unknown workflow switch: {key}")
    prefs.set(key, "1" if value else "0")


@dataclass(frozen=True)
class RecentBatch:
    path: Path
    exists: bool


@dataclass(frozen=True)
class ExportDefaults:
    """Starting choices for a new batch; existing batches use their manifest."""

    folder: str = ""
    format: Format = "jpeg"
    jpeg_size: JpegSize = "3600"  # equals "full" at 3600 dpi; caps a 7200 dpi scan at 3600


def export_defaults(prefs: Prefs) -> ExportDefaults:
    fmt = prefs.get(EXPORT_FORMAT_KEY)
    size = prefs.get(EXPORT_JPEG_SIZE_KEY)
    return ExportDefaults(
        folder=prefs.get(EXPORT_FOLDER_KEY, "") or "",
        format=cast(Format, fmt) if fmt in ("jpeg", "png8", "tiff16") else "jpeg",
        jpeg_size=cast(JpegSize, size) if size in ("full", "3600") else "3600",
    )


def remember_export(prefs: Prefs, folder: str, fmt: Format, jpeg_size: JpegSize) -> None:
    prefs.set(EXPORT_FOLDER_KEY, folder)
    prefs.set(EXPORT_FORMAT_KEY, fmt)
    prefs.set(EXPORT_JPEG_SIZE_KEY, jpeg_size)


def _stored(prefs: Prefs) -> list[str]:
    try:
        data = json.loads(prefs.get(RECENT_KEY, "[]") or "[]")
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, str)]


def recent_batches(prefs: Prefs) -> tuple[RecentBatch, ...]:
    return tuple(RecentBatch(Path(p), Path(p).exists()) for p in _stored(prefs))


def remember_batch(prefs: Prefs, path: Path) -> None:
    kept = [p for p in _stored(prefs) if p != str(path)]
    prefs.set(RECENT_KEY, json.dumps([str(path), *kept][:MAX_RECENT]))


def forget_batch(prefs: Prefs, path: Path) -> None:
    prefs.set(RECENT_KEY, json.dumps([p for p in _stored(prefs) if p != str(path)]))


def batch_subfolder(parent: Path, name: str) -> Path:
    """The folder a new batch named ``name`` is created in; never leaves ``parent``."""
    last = name.replace("\\", "/").split("/")[-1].strip().lstrip(".").strip()
    return parent / (last or FALLBACK_NAME)
