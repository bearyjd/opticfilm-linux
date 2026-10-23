"""Headless smoke test of the whole window on the fake scanner (needs PySide6 and Kirigami).

Run with: QT_QPA_PLATFORM=offscreen pytest -m qt
"""

import os
import time

import pytest

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QUrl  # noqa: E402
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
def window(qapp, sweep600_dir):
    controller = Controller(scanner_factory(sweep600_dir))
    bridge = Bridge(controller, MemoryPrefs(), demo_dpi=600)
    provider = PreviewProvider(lambda n, p, k: controller.preview_path(n, p, k))
    engine, warnings = create_engine(bridge, provider)
    assert engine.rootObjects(), warnings
    yield bridge, warnings
    bridge.shutdown()
    engine.deleteLater()


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
