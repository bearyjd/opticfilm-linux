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
from shiboken6 import getCppPointer, isValid  # noqa: E402

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

    def build(size=(1220, 780), **switches):
        config = DriverConfig(driver_dir=FAKE_DRIVER, log_dir=tmp_path / "logs")
        controller = Controller(lambda _dpi: DriverScanner(config), **switches)
        bridge = Bridge(controller, MemoryPrefs(), demo_dpi=600)
        provider = PreviewProvider(lambda n, p, k: controller.preview_path(n, p, k))
        engine, warnings = create_engine(bridge, provider, size=size)
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


_VISUAL_REFERENCES = []  # wrappers kept alive: a collected one can delete a live item (abort)


def _visual(item, name):
    """Search the visual tree: Repeater delegates are not QObject children of the window."""
    pending, visited = [item], set()
    while pending:
        current = pending.pop()
        if not isValid(current):
            continue
        pointer = getCppPointer(current)[0]
        if pointer in visited:
            continue
        visited.add(pointer)
        if current is not item and current.objectName() == name:
            return current
        children = current.childItems()
        _VISUAL_REFERENCES.extend(children)
        pending.extend(children)
    return None


def _item(root, name):
    return root.findChild(QQuickItem, name)


def _thumb_label(root, position):
    """The placeholder of a contact-sheet tile (delegates are found below the sheet only)."""
    thumb = _visual(_item(root, "sheetView"), f"slideThumb_1_{position}")
    return None if thumb is None else _visual(thumb, "thumbPlaceholder")


def _click_at(root, item, x, y):
    assert item is not None and item.isVisible() and item.isEnabled()
    point = item.mapToScene(QPointF(x, y))
    QTest.mouseClick(
        root,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(int(point.x()), int(point.y())),
    )


def _click(root, item):
    _click_at(root, item, item.width() / 2, item.height() / 2)


COUNTS = [4, 3, 2, 1]  # the segments, left to right


def _click_count(root, count):
    """Press the "count" segment where it is drawn: the segments share the control's width."""
    control = _item(root, "slidesInHolder")
    width = control.width() / len(COUNTS)
    _click_at(root, control, width * (COUNTS.index(count) + 0.5), control.height() / 2)


def _scan(bridge, tmp_path):
    bridge.scan()
    _wait(bridge, lambda: bridge.trays.rowCount() == 1 and not bridge.scanPending)
    return json.loads((tmp_path / "argv.json").read_text())


def test_switched_off_the_selector_is_not_in_the_window(window, tmp_path):
    bridge, root, warnings = window()
    assert not bridge.skipEmptyEnabled
    for name in ("scanOpeningsSelector", "slidesInHolder", "openingToggle1"):
        assert root.findChild(QObject, name) is None, name
    argv = _scan(bridge, tmp_path)
    assert argv[:7] == ["scan", "--slides", "1-4", "--dpi", "600", "--ir", "--no-clean"]
    assert warnings == []


def test_two_slides_in_this_holder_scans_1_to_2_and_resets_to_four(window, tmp_path):
    bridge, root, warnings = window(skip_empty=True)
    assert _item(root, "scanOpeningsSelector").isVisible()
    assert not _item(root, "openingToggle1").isVisible()  # no gaps without SKIP_GAPS
    control = _item(root, "slidesInHolder")
    assert control.isVisible() and control.property("model").toVariant() == list("4321")
    assert control.property("currentIndex") == 0
    _click_count(root, 2)
    _wait(bridge, lambda: list(bridge.scanOpenings) == [1, 2])
    argv = _scan(bridge, tmp_path)
    assert argv[argv.index("--slides") + 1] == "1-2"
    _wait(bridge, lambda: list(bridge.scanOpenings) == [1, 2, 3, 4])
    _wait(bridge, lambda: control.property("currentIndex") == 0)
    _wait(bridge, lambda: bridge.selectedTray == 1 and bridge.slides.rowCount() == 4)
    for position in (3, 4):
        _wait(bridge, lambda position=position: _thumb_label(root, position) is not None)
        label = _thumb_label(root, position)
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


def test_values_that_are_not_whole_openings_are_refused_by_the_bridge(window):
    bridge, _root, _warnings = window(skip_empty=True)
    for bad in ([1.9], [True], ["1"], [None]):
        bridge.setScanOpenings(bad)
        _wait(bridge, lambda: bridge.hasBanner)
        assert list(bridge.scanOpenings) == [1, 2, 3, 4], bad
        bridge.dismissBanner()


def test_mark_empty_is_disabled_for_a_skipped_slide(window, tmp_path, monkeypatch):
    from opticfilm_linux.app.qt import bridge as bridge_module

    bridge, root, _warnings = window(skip_empty=True)
    _scan(bridge, tmp_path)
    _wait(bridge, lambda: bridge.selectedTray == 1 and bridge.slides.rowCount() == 4)
    bridge.selectSlide(1, 1, 0)
    _wait(bridge, lambda: _item(root, "markEmpty_1") is not None)
    button = _item(root, "markEmpty_1")
    _wait(bridge, lambda: button.property("enabled") is True)
    real = bridge_module.focused_slide
    monkeypatch.setattr(
        bridge_module, "focused_slide", lambda state: {**real(state), "status": "skipped"}
    )
    bridge.changed.emit()
    _wait(bridge, lambda: button.property("enabled") is False)


def _scene_rect(item):
    corner = item.mapToScene(QPointF(0, 0))
    return corner.x(), corner.y(), item.width(), item.height()


@pytest.mark.parametrize("size", [(800, 700), (1920, 1080)], ids=["compact", "wide"])
def test_the_selector_with_both_switches_fits_and_keeps_scan_in_view(window, size):
    bridge, root, warnings = window(size=size, skip_empty=True, skip_gaps=True)
    QTest.qWait(100)
    QCoreApplication.processEvents()
    width = root.width()
    names = ["scanOpeningsSelector", *(f"openingToggle{p}" for p in (1, 2, 3, 4))]
    for name in [*names, "ejectButton", "primaryActionButton"]:
        item = _item(root, name)
        assert item is not None and item.isVisible(), name
        x, _y, w, _h = _scene_rect(item)
        assert x >= 0 and x + w <= width + 0.5, (name, x, w, width)
        assert w + 0.5 >= item.implicitWidth(), (name, w, item.implicitWidth())
    assert warnings == []
