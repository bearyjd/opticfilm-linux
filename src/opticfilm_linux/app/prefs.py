"""Remembered preferences and the recent-batches list, behind a tiny storage interface.

The Qt layer backs ``Prefs`` with ``QSettings``; tests use ``MemoryPrefs``.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

DPI_CHOICES = (600, 1200, 2400, 3600, 7200)
DEFAULT_DPI = 3600
MAX_RECENT = 10
RECENT_KEY = "recent_batches"
FALLBACK_NAME = "batch"
THEME_KEY = "theme"
DEFAULT_THEME = "dark"
THEMES = ("dark", "light")


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


@dataclass(frozen=True)
class RecentBatch:
    path: Path
    exists: bool


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
