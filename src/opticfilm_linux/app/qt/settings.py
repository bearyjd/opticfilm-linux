"""``Prefs`` backed by QSettings: the only thing the app stores outside the batch."""

from PySide6.QtCore import QSettings


class QSettingsPrefs:
    def __init__(self, settings: QSettings | None = None) -> None:
        self._settings = settings or QSettings()

    def get(self, key: str, default: str | None = None) -> str | None:
        value = self._settings.value(key, default)
        return None if value is None else str(value)

    def set(self, key: str, value: str) -> None:
        self._settings.setValue(key, value)
        self._settings.sync()
