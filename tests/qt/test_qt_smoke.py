"""Headless smoke test of the whole window on the fake scanner (needs PySide6 and Kirigami).

Run with: QT_QPA_PLATFORM=offscreen pytest -m qt
"""

import os
import time

import pytest

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QMetaObject, QObject, QPoint, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from opticfilm_linux.app.controller import Controller  # noqa: E402
from opticfilm_linux.app.prefs import MemoryPrefs  # noqa: E402
from opticfilm_linux.app.qt.app import create_engine, scanner_factory  # noqa: E402
from opticfilm_linux.app.qt.bridge import Bridge  # noqa: E402
from opticfilm_linux.app.qt.images import PreviewProvider  # noqa: E402

TIMEOUT = 20.0


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["smoke"])


def _wait(bridge, condition, timeout=TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        bridge.poll()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for the window")


@pytest.fixture
def loaded(qapp, sweep600_dir):
    """The bridge, the QML engine with the window loaded, and its collected warnings."""
    controller = Controller(scanner_factory(sweep600_dir))
    bridge = Bridge(controller, MemoryPrefs(), demo_dpi=600)
    provider = PreviewProvider(lambda n, p, k: controller.preview_path(n, p, k))
    engine, warnings = create_engine(bridge, provider)
    assert engine.rootObjects(), warnings
    yield bridge, engine, warnings
    bridge.shutdown()
    engine.deleteLater()


@pytest.fixture
def window(loaded):
    bridge, _engine, warnings = loaded
    return bridge, warnings


def test_scan_review_and_export_on_the_fake_scanner(window, tmp_path):
    bridge, warnings = window
    parent = QUrl.fromLocalFile(str(tmp_path)).toString()
    bridge.newBatch(parent, "Smoke", "", "", 600)
    _wait(bridge, lambda: bridge.hasBatch)
    _wait(bridge, lambda: bridge.canScan)
    bridge.scan()
    _wait(bridge, lambda: bridge.trays.rowCount() == 1 and not bridge.scanPending)
    _wait(bridge, lambda: bridge.selectedTray == 1 and bridge.slides.rowCount() == 4)
    bridge.acceptAll()
    out = tmp_path / "out"
    bridge.startExport(QUrl.fromLocalFile(str(out)).toString(), "tiff16")
    _wait(bridge, lambda: bridge.exportFinished)
    assert bridge.exportWritten > 0 and len(list(out.iterdir())) == bridge.exportWritten
    assert (
        sorted(
            {w.split("plustek-slide-scanner/src/opticfilm_linux/app/qml/")[-1] for w in warnings}
        )
        == []
    )


def test_second_window_on_the_same_batch_reports_in_use(qapp, sweep600_dir, tmp_path):
    def make():
        controller = Controller(scanner_factory(sweep600_dir))
        return Bridge(controller, MemoryPrefs(), demo_dpi=600)

    first, second = make(), make()
    try:
        first.newBatch(QUrl.fromLocalFile(str(tmp_path)).toString(), "Shared", "", "", 600)
        _wait(first, lambda: first.hasBatch)
        second.openBatch(str(tmp_path / "Shared"), False)
        _wait(second, lambda: second.hasBanner)
        assert second.bannerLocked and not second.hasBatch
    finally:
        first.shutdown()
        second.shutdown()


def _scanned(bridge, tmp_path, name):
    bridge.newBatch(QUrl.fromLocalFile(str(tmp_path)).toString(), name, "", "", 600)
    _wait(bridge, lambda: bridge.hasBatch)
    _wait(bridge, lambda: bridge.canScan)
    bridge.scan()
    _wait(bridge, lambda: bridge.trays.rowCount() == 1 and not bridge.scanPending)
    _wait(bridge, lambda: bridge.selectedTray == 1 and bridge.slides.rowCount() == 4)
    return tmp_path / name


def _first_slide(directory):
    from opticfilm_linux.batch.store import BatchStore

    with BatchStore.open(directory, readonly=True) as store:
        return store.snapshot().trays[0].slides[0]


def test_a_pending_slider_edit_is_saved_before_the_batch_closes(window, tmp_path):
    bridge, _warnings = window
    directory = _scanned(bridge, tmp_path, "Closing")
    bridge.adjust(1, 1, "brightness", 1.0)  # still inside the debounce pause
    bridge.closeBatch()
    _wait(bridge, lambda: not bridge.hasBatch)
    assert _first_slide(directory).settings.brightness_ev == 1.0


def test_a_pending_slider_edit_is_saved_when_the_window_shuts_down(window, tmp_path):
    bridge, _warnings = window
    directory = _scanned(bridge, tmp_path, "Quitting")
    bridge.adjust(1, 1, "brightness", 1.5)
    bridge.shutdown()
    assert _first_slide(directory).settings.brightness_ev == 1.5


def test_export_waits_for_a_pending_slider_edit(window, tmp_path):
    bridge, _warnings = window
    directory = _scanned(bridge, tmp_path, "Exporting")
    bridge.acceptAll()
    bridge.adjust(1, 1, "brightness", 1.0)
    bridge.startExport(QUrl.fromLocalFile(str(tmp_path / "out")).toString(), "tiff16")
    _wait(bridge, lambda: bridge.exportFinished)
    bridge.shutdown()
    slide = _first_slide(directory)
    assert slide.settings.brightness_ev == 1.0 and slide.exported


def test_orientation_from_the_new_batch_form_slide_mirror_and_batch_dialog(loaded, tmp_path):
    bridge, engine, warnings = loaded
    bridge.newBatch(QUrl.fromLocalFile(str(tmp_path)).toString(), "Turned", "", "", 600, 90, True)
    _wait(bridge, lambda: bridge.hasBatch)
    assert (bridge.batchRotation, bridge.batchMirror) == (90, True)
    _wait(bridge, lambda: bridge.canScan)
    bridge.scan()
    _wait(bridge, lambda: bridge.selectedTray == 1 and bridge.slides.rowCount() == 4)
    _wait(bridge, lambda: bridge.trays.rowCount() == 1 and not bridge.scanPending)
    bridge.mirror(1, 1)
    role = next(k for k, v in bridge.slides.roleNames().items() if bytes(v) == b"mirror")
    _wait(bridge, lambda: bridge.slides.data(bridge.slides.index(0), role) is True)
    root = engine.rootObjects()[0]
    dialog = root.findChild(QObject, "orientationDialog")
    assert dialog is not None
    QMetaObject.invokeMethod(dialog, "open")
    _wait(bridge, lambda: dialog.property("opened"))
    _wait(bridge, lambda: _all_reviewed(tmp_path / "Turned"))
    bridge.setBatchOrientation(180, False)
    _wait(bridge, lambda: (bridge.batchRotation, bridge.batchMirror) == (180, False))
    assert not bridge.hasBanner
    assert warnings == []


def _all_reviewed(directory):
    from opticfilm_linux.batch.store import BatchStore

    with BatchStore.open(directory, readonly=True) as store:
        return all(t.stage.value == "review" for t in store.snapshot().trays)


def _click(root, name):
    item = root.findChild(QQuickItem, name)
    assert item is not None and item.isVisible(), name
    centre = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(int(centre.x()), int(centre.y())),
    )


def test_the_batch_orientation_dialog_shows_the_current_orientation_and_applies(loaded, tmp_path):
    bridge, engine, warnings = loaded
    bridge.newBatch(QUrl.fromLocalFile(str(tmp_path)).toString(), "Dialog", "", "", 600, 270, True)
    _wait(bridge, lambda: bridge.hasBatch)
    root = engine.rootObjects()[0]
    _wait(bridge, lambda: root.findChild(QObject, "orientationDialog") is not None)
    dialog = root.findChild(QObject, "orientationDialog")
    QMetaObject.invokeMethod(dialog, "open")
    _wait(bridge, lambda: dialog.property("opened"))
    rotation = root.findChild(QObject, "orientationRotation")
    mirror = root.findChild(QObject, "orientationMirror")
    assert rotation.property("currentText") == "270" and mirror.property("checked") is True
    _click(root, "orientationMirror")
    _wait(bridge, lambda: mirror.property("checked") is False)
    _click(root, "orientationApply")
    _wait(bridge, lambda: (bridge.batchRotation, bridge.batchMirror) == (270, False))
    _wait(bridge, lambda: not dialog.property("visible"))
    assert not bridge.hasBanner and warnings == []


def _prop(root, name, prop):
    """Looked up each time: the page holding the toolbar may be rebuilt meanwhile."""
    item = root.findChild(QQuickItem, name)
    return (
        None if item is None else (item.isVisible() if prop == "visible" else item.property(prop))
    )


def test_holder_buttons_follow_the_flags_on_a_ready_scanner(loaded, tmp_path):
    bridge, engine, warnings = loaded
    bridge.newBatch(QUrl.fromLocalFile(str(tmp_path)).toString(), "Holder", "", "", 600)
    _wait(bridge, lambda: bridge.hasBatch and bridge.canScan)
    root = engine.rootObjects()[0]
    _wait(bridge, lambda: _prop(root, "ejectButton", "enabled") is True)
    assert bridge.canEject and not bridge.canLoadNext and not bridge.nextStripEnabled
    assert _prop(root, "loadNextButton", "enabled") is False
    assert _prop(root, "loadHelpButton", "visible") and not _prop(root, "loadHelpLink", "visible")
    _click(root, "ejectButton")
    _wait(bridge, lambda: bridge.canEject)
    assert not bridge.hasBanner and warnings == []


def test_a_not_loaded_scanner_offers_the_help_link_and_load_next(qapp, sweep600_dir, tmp_path):
    from opticfilm_linux.scanner.base import ScannerStatus
    from opticfilm_linux.scanner.fake import FakeScanner

    calls: list[str] = []

    class Recording(FakeScanner):
        def eject(self) -> None:
            calls.append("eject")

        def load_next_strip(self) -> None:
            calls.append("load")

    scanner = Recording(sweep600_dir, (ScannerStatus.NOT_READY,))
    controller = Controller(lambda _dpi: scanner, next_strip=True)
    bridge = Bridge(controller, MemoryPrefs(), demo_dpi=600, load_command_text="of135i load now")
    provider = PreviewProvider(lambda n, p, k: controller.preview_path(n, p, k))
    engine, warnings = create_engine(bridge, provider)
    try:
        bridge.newBatch(QUrl.fromLocalFile(str(tmp_path)).toString(), "Loose", "", "", 600)
        _wait(bridge, lambda: bridge.hasBatch and bridge.canLoadNext)
        root = engine.rootObjects()[0]
        _wait(bridge, lambda: _prop(root, "loadNextButton", "enabled") is True)
        assert not bridge.canScan and bridge.canEject and bridge.nextStripEnabled
        _wait(bridge, lambda: _prop(root, "loadHelpLink", "visible"))
        _wait(bridge, lambda: "not loaded" in (_prop(root, "scannerStatusText", "text") or ""))
        assert _prop(root, "scannerStatusText", "truncated") is False
        for name in ("loadHelpLink", "ejectButton", "loadNextButton", "loadHelpButton"):
            item = root.findChild(QQuickItem, name)
            assert item.width() >= item.implicitWidth() - 0.5, name
        _click(root, "loadNextButton")
        _wait(bridge, lambda: calls == ["load"])
        _wait(bridge, lambda: _prop(root, "ejectButton", "enabled") is True)
        _click(root, "ejectButton")
        _wait(bridge, lambda: calls == ["load", "eject"])
        _click(root, "loadHelpLink")
        dialog = root.findChild(QObject, "loadHelpDialog")
        _wait(bridge, lambda: dialog.property("opened"))
        field = root.findChild(QObject, "loadCommandField")
        assert field.property("text") == "of135i load now" == bridge.loadCommand
        _click(root, "copyLoadCommand")
        _wait(bridge, lambda: QApplication.clipboard().text() == "of135i load now")
        assert not bridge.hasBanner and warnings == []
    finally:
        bridge.shutdown()
        engine.deleteLater()


def test_the_default_command_is_the_distrobox_load(window):
    bridge, warnings = window
    assert "of135i load" in bridge.loadCommand and "distrobox enter dev" in bridge.loadCommand
    assert warnings == []


def test_the_help_dialog_hides_the_power_steps_while_a_scan_runs(qapp, sweep600_dir, tmp_path):
    import threading

    from opticfilm_linux.scanner.fake import FakeScanner

    gate = threading.Event()
    scanner = FakeScanner(sweep600_dir, before_scan=lambda: gate.wait(30))
    controller = Controller(lambda _dpi: scanner)
    bridge = Bridge(controller, MemoryPrefs(), demo_dpi=600)
    provider = PreviewProvider(lambda n, p, k: controller.preview_path(n, p, k))
    engine, warnings = create_engine(bridge, provider)
    try:
        bridge.newBatch(QUrl.fromLocalFile(str(tmp_path)).toString(), "Busy", "", "", 600)
        _wait(bridge, lambda: bridge.hasBatch and bridge.canScan)
        root = engine.rootObjects()[0]
        _wait(bridge, lambda: root.findChild(QObject, "loadHelpDialog") is not None)
        dialog = root.findChild(QObject, "loadHelpDialog")
        QMetaObject.invokeMethod(dialog, "open")
        _wait(bridge, lambda: dialog.property("opened"))
        assert _prop(root, "loadSteps", "visible") and not _prop(root, "loadBusyWarning", "visible")
        bridge.scan()
        _wait(bridge, lambda: bridge.scanPending)
        _wait(bridge, lambda: _prop(root, "loadBusyWarning", "visible") is True)
        assert not _prop(root, "loadSteps", "visible")
        assert _prop(root, "copyLoadCommand", "enabled") is False
        gate.set()
        _wait(bridge, lambda: not bridge.scanPending)
        _wait(bridge, lambda: _prop(root, "loadSteps", "visible") is True)
        assert not _prop(root, "loadBusyWarning", "visible") and warnings == []
    finally:
        gate.set()
        bridge.shutdown()
        engine.deleteLater()
