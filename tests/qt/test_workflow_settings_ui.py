"""The workflow settings dialog: saved switches, env locks, live effect on the controller.

Run with: QT_QPA_PLATFORM=offscreen pytest -m qt tests/qt/test_workflow_settings_ui.py
"""

import os
import time

import pytest

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QMetaObject, QObject, QPoint, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from opticfilm_linux.app import prefs as preferences  # noqa: E402
from opticfilm_linux.app.controller import Controller  # noqa: E402
from opticfilm_linux.app.prefs import MemoryPrefs  # noqa: E402
from opticfilm_linux.app.qt.app import create_engine  # noqa: E402
from opticfilm_linux.app.qt.bridge import Bridge  # noqa: E402
from opticfilm_linux.app.qt.images import PreviewProvider  # noqa: E402
from opticfilm_linux.app.workflow_settings import startup_switches  # noqa: E402
from opticfilm_linux.scanner.fake import FakeScanner  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["workflow-settings"])


@pytest.fixture
def window(qapp, sweep600_dir, tmp_path, monkeypatch):
    for name in ("OPTICFILM_AUTO_EJECT", "OPTICFILM_SKIP_EMPTY", "OPTICFILM_SKIP_GAPS"):
        monkeypatch.delenv(name, raising=False)
    made = []

    def build(prefs=None, open_batch=True, **env):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        prefs = prefs or MemoryPrefs()
        controller = Controller(lambda _dpi: FakeScanner(sweep600_dir), **startup_switches(prefs))
        bridge = Bridge(controller, prefs, demo_dpi=600)
        provider = PreviewProvider(lambda n, p, k: controller.preview_path(n, p, k))
        engine, warnings = create_engine(bridge, provider)
        assert engine.rootObjects(), warnings
        made.append((bridge, engine))
        if open_batch:
            bridge.newBatch(QUrl.fromLocalFile(str(tmp_path)).toString(), "B", "", "", 600)
            _wait_for(lambda: bridge.poll() or bridge.hasBatch)
            QCoreApplication.processEvents()
        return bridge, controller, engine.rootObjects()[0], prefs

    yield build
    for bridge, engine in made:
        bridge.shutdown()
        engine.deleteLater()


def _find(root, name):
    item = root.findChild(QObject, name)
    assert item is not None, name
    return item


def _open_dialog(root):
    dialog = _find(root, "workflowSettingsDialog")
    QMetaObject.invokeMethod(dialog, "open", Qt.ConnectionType.DirectConnection)
    _wait_for(lambda: dialog.property("visible") is True)
    QCoreApplication.processEvents()


def _toggle(root, name):
    """A real click on the switch, as a user would do (needs the dialog open)."""
    _open_dialog(root)
    item = _find(root, name)
    center = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    point = QPoint(int(center.x()), int(center.y()))
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    QCoreApplication.processEvents()


def _wait_for(condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def test_fresh_profile_shows_everything_off(window):
    bridge, _controller, root, _prefs = window()
    assert not bridge.autoEjectEnabled and not bridge.skipEmptyEnabled
    assert not bridge.autoExportDefault
    for name in ("autoExportDefaultSwitch", "autoEjectSwitch", "skipEmptySwitch"):
        switch = _find(root, name)
        assert switch.property("checked") is False
        assert switch.property("enabled") is True


def test_settings_action_exists_and_dialog_opens(window):
    _bridge, _controller, root, _prefs = window()
    assert _find(root, "workflowSettingsAction") is not None
    dialog = _find(root, "workflowSettingsDialog")
    QMetaObject.invokeMethod(dialog, "open", Qt.ConnectionType.DirectConnection)
    QCoreApplication.processEvents()
    assert dialog.property("visible") is True


def test_toggles_save_and_apply_live(window):
    bridge, controller, root, prefs = window()
    _toggle(root, "autoEjectSwitch")
    assert controller.auto_eject_enabled is True
    assert preferences.read_switch(prefs, preferences.AUTO_EJECT_KEY) is True
    _toggle(root, "skipEmptySwitch")
    assert controller.skip_empty_enabled is True
    assert bridge.skipEmptyEnabled is True
    _toggle(root, "autoEjectSwitch")
    assert controller.auto_eject_enabled is False
    assert preferences.read_switch(prefs, preferences.AUTO_EJECT_KEY) is False


def test_auto_export_default_leaves_the_open_batch_alone(window):
    bridge, controller, root, prefs = window()
    _toggle(root, "autoExportDefaultSwitch")
    assert preferences.read_switch(prefs, preferences.AUTO_EXPORT_KEY) is True
    assert bridge.autoExportDefault is True
    assert controller.state().auto_export is False  # the open batch keeps its own value


def test_new_batch_form_starts_from_the_saved_default(window):
    prefs = MemoryPrefs()
    _bridge, _controller, root, _ = window(prefs, open_batch=False)
    assert _find(root, "newBatchAutoExport").property("checked") is False
    preferences.remember_switch(prefs, preferences.AUTO_EXPORT_KEY, True)
    _bridge, _controller, root, _ = window(prefs, open_batch=False)
    assert _find(root, "newBatchAutoExport").property("checked") is True


def test_saved_values_load_at_startup(window):
    prefs = MemoryPrefs()
    preferences.remember_switch(prefs, preferences.AUTO_EJECT_KEY, True)
    bridge, controller, root, _ = window(prefs)
    assert controller.auto_eject_enabled is True
    assert _find(root, "autoEjectSwitch").property("checked") is True
    assert bridge.autoEjectLockedBy == ""


def test_env_locks_the_switch_and_names_the_variable(window):
    prefs = MemoryPrefs()
    bridge, controller, root, _ = window(prefs, OPTICFILM_AUTO_EJECT="1")
    switch = _find(root, "autoEjectSwitch")
    assert switch.property("enabled") is False
    assert switch.property("checked") is True
    assert bridge.autoEjectLockedBy == "OPTICFILM_AUTO_EJECT"
    _open_dialog(root)
    note = _find(root, "autoEjectSettingLocked")
    assert note.property("visible") is True
    assert "OPTICFILM_AUTO_EJECT" in note.property("text")
    bridge.setAutoEject(False)  # even a direct call cannot override it
    assert controller.auto_eject_enabled is True
    assert preferences.read_switch(prefs, preferences.AUTO_EJECT_KEY) is False


def test_env_off_beats_a_saved_on(window):
    prefs = MemoryPrefs()
    preferences.remember_switch(prefs, preferences.SKIP_EMPTY_KEY, True)
    bridge, controller, root, _ = window(prefs, OPTICFILM_SKIP_EMPTY="0")
    assert controller.skip_empty_enabled is False
    assert _find(root, "skipEmptySwitch").property("enabled") is False
    assert bridge.skipEmptyLockedBy == "OPTICFILM_SKIP_EMPTY"


def test_wording_says_unproven_on_the_scanner(window):
    _bridge, _controller, root, _prefs = window()
    for name in ("autoExportSetting", "autoEjectSetting"):
        labels = [c.property("text") for c in _find(root, name).children()]
        assert any("not yet proven" in (t or "") for t in labels), name
