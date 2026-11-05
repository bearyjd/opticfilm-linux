"""The "Slides in this holder" selector (OPTICFILM_SKIP_EMPTY), on the fake driver.

Run with: QT_QPA_PLATFORM=offscreen pytest -m qt tests/qt/test_scan_openings_selector.py
"""

import json
import os
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QObject, QPoint, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from opticfilm_linux.app.controller import Controller  # noqa: E402
from opticfilm_linux.app.prefs import MemoryPrefs  # noqa: E402
from opticfilm_linux.app.qt.app import create_engine  # noqa: E402
from opticfilm_linux.app.qt.bridge import Bridge  # noqa: E402
from opticfilm_linux.app.qt.images import PreviewProvider  # noqa: E402
from opticfilm_linux.scanner.driver import DriverConfig, DriverScanner  # noqa: E402

FAKE_DRIVER = Path(__file__).resolve().parents[1] / "fake_driver"
TIMEOUT = 30.0


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["selector"])


@pytest.fixture
def window(qapp, sweep600_dir, tmp_path, monkeypatch):
    """Build the window on the real driver backend running the fake driver, with given switches."""
    monkeypatch.setenv("FAKE_DRIVER_SWEEP", str(sweep600_dir))
    monkeypatch.setenv("FAKE_DRIVER_ARGV", str(tmp_path / "argv.json"))
    for name in ("OPTICFILM_SKIP_EMPTY", "OPTICFILM_SKIP_GAPS", "OPTICFILM_DRIVER_CLEAN"):
        monkeypatch.delenv(name, raising=False)
    made = []

    def build(**switches):
        config = DriverConfig(driver_dir=FAKE_DRIVER, log_dir=tmp_path / "logs")
        controller = Controller(lambda _dpi: DriverScanner(config), **switches)
        bridge = Bridge(controller, MemoryPrefs(), demo_dpi=600)
        provider = PreviewProvider(lambda n, p, k: controller.preview_path(n, p, k))
        engine, warnings = create_engine(bridge, provider, size=(1220, 780))
        assert engine.rootObjects(), warnings
        made.append((bridge, engine))
        bridge.newBatch(QUrl.fromLocalFile(str(tmp_path)).toString(), "Holder", "", "", 600)
        _wait(bridge, lambda: bridge.hasBatch and bridge.canScan)
        return bridge, engine.rootObjects()[0], warnings

    yield build
    for bridge, engine in made:
        bridge.shutdown()
        engine.deleteLater()


def _wait(bridge, condition, timeout=TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        bridge.poll()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def _visual(item, name):
    """Search the visual tree: Repeater delegates are not QObject children of the window."""
    for child in item.childItems():
        if child.objectName() == name:
            return child
        found = _visual(child, name)
        if found is not None:
            return found
    return None


def _item(root, name):
    found = root.findChild(QQuickItem, name)
    return found if found is not None else _visual(root.contentItem(), name)


def _click(root, item):
    assert item is not None and item.isVisible() and item.isEnabled()
    centre = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(int(centre.x()), int(centre.y())),
    )


def _segment(root, label):
    control = _item(root, "slidesInHolder")
    segments = [_visual(control, f"segment{i}") for i in range(4)]
    return next(s for s in segments if s.property("text") == label)


def _scan(bridge, tmp_path):
    bridge.scan()
    _wait(bridge, lambda: bridge.trays.rowCount() == 1 and not bridge.scanPending)
    return json.loads((tmp_path / "argv.json").read_text())


def test_switched_off_the_selector_is_not_in_the_window(window, tmp_path):
    bridge, root, warnings = window()
    assert not bridge.skipEmptyEnabled
    for name in ("scanOpeningsSelector", "slidesInHolder", "openingToggle1"):
        assert root.findChild(QObject, name) is None and _item(root, name) is None, name
    argv = _scan(bridge, tmp_path)
    assert argv[:7] == ["scan", "--slides", "1-4", "--dpi", "600", "--ir", "--no-clean"]
    assert warnings == []


def test_two_slides_in_this_holder_scans_1_to_2_and_resets_to_four(window, tmp_path):
    bridge, root, warnings = window(skip_empty=True)
    assert _item(root, "scanOpeningsSelector").isVisible()
    assert _item(root, "openingToggle1") is None  # no gaps without SKIP_GAPS
    control = _item(root, "slidesInHolder")
    assert [_visual(control, f"segment{i}").property("text") for i in range(4)] == list("4321")
    assert _segment(root, "4").property("checked")
    _click(root, _segment(root, "2"))
    _wait(bridge, lambda: list(bridge.scanOpenings) == [1, 2])
    argv = _scan(bridge, tmp_path)
    assert argv[argv.index("--slides") + 1] == "1-2"
    _wait(bridge, lambda: list(bridge.scanOpenings) == [1, 2, 3, 4])
    _wait(bridge, lambda: _segment(root, "4").property("checked"))
    assert not _segment(root, "2").property("checked")
    _wait(bridge, lambda: bridge.selectedTray == 1 and bridge.slides.rowCount() == 4)
    for position in (3, 4):
        name = f"slideThumb_1_{position}"
        _wait(bridge, lambda name=name: _item(root, name) is not None)
        thumb = _item(root, name)
        label = _visual(thumb, "thumbPlaceholder")
        assert label.property("text") == "Empty (not scanned)" and label.isVisible()
    assert warnings == []


def test_with_gaps_four_toggles_and_the_last_one_cannot_be_cleared(window, tmp_path):
    bridge, root, warnings = window(skip_empty=True, skip_gaps=True)
    assert not _item(root, "slidesInHolder").isVisible()
    toggles = [_item(root, f"openingToggle{p}") for p in (1, 2, 3, 4)]
    assert all(t.isVisible() and t.isEnabled() for t in toggles)
    for opening, expected in ((2, [1, 3, 4]), (1, [3, 4]), (3, [4])):
        _click(root, toggles[opening - 1])
        _wait(bridge, lambda expected=expected: list(bridge.scanOpenings) == expected)
    assert not toggles[3].isEnabled()  # an empty choice is refused: eject instead
    _click(root, toggles[0])
    _wait(bridge, lambda: list(bridge.scanOpenings) == [1, 4])
    argv = _scan(bridge, tmp_path)
    assert argv[argv.index("--slides") + 1] == "1,4"
    _wait(bridge, lambda: list(bridge.scanOpenings) == [1, 2, 3, 4])
    assert warnings == []
