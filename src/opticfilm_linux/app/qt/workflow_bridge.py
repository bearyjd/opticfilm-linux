"""Saved workflow switches for QML: auto-eject, skip-empty, and the new-batch auto-export default.

A switch an environment variable decides is shown as locked and refuses changes; the others
are saved in the preferences and applied to the running controller at once.
"""

from __future__ import annotations

from PySide6.QtCore import Property, Signal, Slot

from opticfilm_linux.app.prefs import (
    AUTO_EJECT_KEY,
    AUTO_EXPORT_KEY,
    SKIP_EMPTY_KEY,
    read_switch,
    remember_switch,
)
from opticfilm_linux.app.qt.start_bridge import StartBridge
from opticfilm_linux.app.workflow_settings import ENV_NAMES


class WorkflowBridge(StartBridge):
    workflowSettingsChanged = Signal()  # noqa: N815

    def _env_name(self, switch: str) -> str:
        return ENV_NAMES[switch] if switch in self._controller.env_locked else ""

    autoEjectEnabled = Property(  # noqa: N815
        bool, lambda self: self._controller.auto_eject_enabled, notify=workflowSettingsChanged
    )
    autoEjectLockedBy = Property(  # noqa: N815
        str, lambda self: self._env_name("auto_eject"), notify=workflowSettingsChanged
    )
    skipEmptyEnabled = Property(  # noqa: N815
        bool, lambda self: self._controller.skip_empty_enabled, notify=workflowSettingsChanged
    )
    skipEmptyLockedBy = Property(  # noqa: N815
        str, lambda self: self._env_name("skip_empty"), notify=workflowSettingsChanged
    )
    skipGapsEnabled = Property(  # noqa: N815
        bool, lambda self: self._controller.skip_gaps_enabled, notify=workflowSettingsChanged
    )
    autoExportDefault = Property(  # noqa: N815
        bool,
        lambda self: read_switch(self._prefs, AUTO_EXPORT_KEY),
        notify=workflowSettingsChanged,
    )

    @Slot(bool)
    def setAutoEject(self, on: bool) -> None:  # noqa: N802
        if self._controller.set_auto_eject(on):
            remember_switch(self._prefs, AUTO_EJECT_KEY, on)
        self.workflowSettingsChanged.emit()

    @Slot(bool)
    def setSkipEmpty(self, on: bool) -> None:  # noqa: N802
        if self._controller.set_skip_empty(on):
            remember_switch(self._prefs, SKIP_EMPTY_KEY, on)
        self.workflowSettingsChanged.emit()

    @Slot(bool)
    def setAutoExportDefault(self, on: bool) -> None:  # noqa: N802
        """Only the starting choice of new batches; an open or old batch keeps its own value."""
        remember_switch(self._prefs, AUTO_EXPORT_KEY, on)
        self.workflowSettingsChanged.emit()
