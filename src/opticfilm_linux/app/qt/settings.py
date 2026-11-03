"""``Prefs`` backed by QSettings: the only thing the app stores outside the batch."""

from __future__ import annotations

from typing import TYPE_CHECKING

from opticfilm_linux.app.qt.window_geometry import SavedGeometry

if TYPE_CHECKING:
    from PySide6.QtCore import QSettings


class QSettingsPrefs:
    def __init__(self, settings: QSettings | None = None) -> None:
        if settings is None:
            from PySide6.QtCore import QSettings  # noqa: PLC0415

            settings = QSettings()
        self._settings = settings

    def get(self, key: str, default: str | None = None) -> str | None:
        value = self._settings.value(key, default)
        return None if value is None else str(value)

    def set(self, key: str, value: str) -> None:
        self._settings.setValue(key, value)
        self._settings.sync()

    def window_geometry(self) -> SavedGeometry | None:
        """Ignore incomplete/corrupt sizes left by old settings or manual edits."""
        try:
            width = int(self.get("window/width", "0"))
            height = int(self.get("window/height", "0"))
        except (TypeError, ValueError):
            return None
        if width <= 0 or height <= 0:
            return None
        maximized = (self.get("window/maximized") or "").lower() in {"true", "1"}
        return SavedGeometry(width, height, maximized)

    def save_window_geometry(self, width: int, height: int, maximized: bool) -> None:
        """Persist dimensions together; position deliberately remains window-manager owned."""
        values = {"width": width, "height": height, "maximized": str(maximized).lower()}
        for key, value in values.items():
            self._settings.setValue(f"window/{key}", value)
        self._settings.sync()
