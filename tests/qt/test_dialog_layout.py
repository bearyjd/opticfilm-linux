"""Small-window workflow popups retain usable bodies and pinned actions."""

import os
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
pytest.importorskip("PySide6")

from dialog_layout_probe import (
    ancestors,
    assert_inside,
    assert_unclipped,
    body_flickable,
    descendants,
    popup_rect,
    scene_rect,
)
from PySide6.QtCore import QMetaObject, QObject, QRectF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from opticfilm_linux.app.controller import Controller
from opticfilm_linux.app.prefs import MemoryPrefs
from opticfilm_linux.app.qt.app import configure_style, scanner_factory
from opticfilm_linux.app.qt.bridge import Bridge
from opticfilm_linux.app.state import ExportView, FreeSpaceView

NAMES = ("Export", "EmptyFrames", "FreeSpace", "Orientation", "LoadHelp", "Confirm")
SIZES = ((720, 540), (800, 600), (1024, 600))
FOOTER_ACTIONS = {
    "Export": {"startExport", "reexportAllAccepted"},
    "EmptyFrames": {"confirmReviewedEmptyFrames"},
    "Orientation": {"orientationApply"},
}


@pytest.fixture
def popup_window(tmp_path):
    app = QApplication.instance() or QApplication(["dialog-layout"])
    configure_style()
    bridge = Bridge(Controller(scanner_factory(None)), MemoryPrefs())
    bridge._timer.stop()
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda entries: warnings.extend(entry.toString() for entry in entries))
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("DialogHarness.qml"))))
    assert engine.rootObjects(), warnings
    root = engine.rootObjects()[0]
    preview = tmp_path / "empty.png"
    Image.new("RGB", (100, 80), "white").save(preview)
    empty = root.findChild(QObject, "emptyFramesDialog")
    empty.setProperty(
        "previews",
        [
            {
                "position": i,
                "fingerprint": "a" * 64,
                "url": QUrl.fromLocalFile(str(preview)).toString(),
            }
            for i in range(1, 9)
        ],
    )
    QTest.qWait(100)
    yield app, bridge, root, warnings
    root.close()
    bridge.shutdown()
    engine.deleteLater()


def open_dialog(root, name, size=(800, 600)):
    root.resize(*size)
    object_name = "emptyFramesDialog" if name == "EmptyFrames" else "sample" + name
    dialog = root.findChild(QObject, object_name)
    assert QMetaObject.invokeMethod(dialog, "open")
    for _ in range(100):
        QTest.qWait(20)
        if dialog.property("opened"):
            break
    assert dialog.property("opened")
    return dialog


def footer_buttons(root, dialog):
    return [
        button
        for button in root.pillButtons(dialog.property("footer")).toVariant()
        if button.isVisible()
    ]


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("name", NAMES)
def test_dialog_bounds_and_footer_actions(popup_window, name, size):
    _, _, root, warnings = popup_window
    dialog = open_dialog(root, name, size)
    assert_layout(root, dialog, size)
    buttons = footer_buttons(root, dialog)
    assert FOOTER_ACTIONS.get(name, set()) <= {button.objectName() for button in buttons}
    assert warnings == []


def assert_layout(root, dialog, size):
    bounds = popup_rect(dialog)
    assert_inside(bounds, QRectF(0, 0, *size))
    assert 0 < dialog.property("preferredWidth") <= size[0] * 0.9
    assert 0 < dialog.property("maximumHeight") <= size[1] * 0.9
    assert bounds.width() <= size[0] * 0.9 + 1
    assert bounds.height() <= size[1] * 0.9 + 1
    buttons = footer_buttons(root, dialog)
    assert buttons, "dialog has no visible footer actions"
    body = body_flickable(dialog)
    for button in buttons:
        assert_unclipped(button, root)
        assert body not in list(ancestors(button))


@pytest.mark.parametrize(
    "name,state,texts",
    [
        ("FreeSpace", FreeSpaceView(((1, 1024),), 1024), {"Remove raw scans", "Cancel"}),
        ("FreeSpace", FreeSpaceView(((1, 1024),), 1024, running=True), {"Stop after this tray"}),
        ("FreeSpace", FreeSpaceView(((1, 1024),), 1024, freed=1024), {"Close"}),
        ("Export", ExportView(True, 0, 1), {"Cancel"}),
        ("Export", ExportView(False, 1, 1, finished=True), {"Close", "Re-export all accepted"}),
    ],
)
def test_operation_state_footer_actions_stay_visible(popup_window, name, state, texts):
    _, bridge, root, warnings = popup_window
    state_key = "free_space" if name == "FreeSpace" else "export"
    bridge._state = replace(bridge._state, **{state_key: state})
    bridge.changed.emit()
    dialog = open_dialog(root, name, (720, 540))
    assert_layout(root, dialog, (720, 540))
    assert texts == {button.property("text") for button in footer_buttons(root, dialog)}
    assert warnings == []


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("name", ["EmptyFrames", "LoadHelp"])
def test_overflow_scrolls_only_the_body(popup_window, name, size):
    _, _, root, warnings = popup_window
    dialog = open_dialog(root, name, size)
    flickable = body_flickable(dialog)
    overflow = flickable.property("contentHeight") - flickable.height()
    assert overflow > 0, "fixture must exercise a scrolling body"
    buttons = footer_buttons(root, dialog)
    assert buttons
    before = [scene_rect(button) for button in buttons]
    flickable.setProperty("contentY", overflow)
    QTest.qWait(50)
    assert flickable.property("contentY") > 0
    assert before == [scene_rect(button) for button in buttons]
    for button in buttons:
        assert_unclipped(button, root)
    assert warnings == []


def test_empty_confirmation_requires_every_current_preview(popup_window):
    _, _, root, warnings = popup_window
    dialog = open_dialog(root, "EmptyFrames")
    button = root.findChild(QObject, "confirmReviewedEmptyFrames")
    assert dialog.property("loaded") == 8
    assert button.property("enabled")
    dialog.setProperty("loaded", 7)
    assert not button.property("enabled")
    dialog.setProperty("loaded", 9)
    assert not button.property("enabled")
    dialog.setProperty("loaded", 8)
    assert button.property("enabled")
    dialog.setProperty("previews", [])
    QTest.qWait(50)
    assert not button.property("enabled")
    assert warnings == []


def test_confirm_opens_with_accept_focus_and_keeps_escape(popup_window):
    _, _, root, warnings = popup_window
    dialog = open_dialog(root, "Confirm")
    buttons = footer_buttons(root, dialog)
    accept = next(button for button in buttons if not button.property("rejecting"))
    assert root.activeFocusItem() is accept
    QTest.keyClick(root, Qt.Key_Return)
    QTest.qWait(300)
    assert not dialog.property("visible") and root.property("confirmations") == 1
    open_dialog(root, "Confirm")
    QTest.keyClick(root, Qt.Key_Escape)
    QTest.qWait(300)
    assert not dialog.property("visible") and root.property("rejections") == 1
    assert warnings == []


@pytest.mark.parametrize("name", ["FreeSpace", "Export"])
def test_running_operations_keep_escape_disabled(popup_window, name):
    _, bridge, root, warnings = popup_window
    dialog = open_dialog(root, name)
    state_key = "free_space" if name == "FreeSpace" else "export"
    state = FreeSpaceView(((1, 1024),), 1024) if name == "FreeSpace" else ExportView(False, 0, 1)
    bridge._state = replace(bridge._state, **{state_key: replace(state, running=True)})
    bridge.changed.emit()
    QTest.qWait(50)
    QTest.keyClick(root, Qt.Key_Escape)
    QTest.qWait(300)
    assert dialog.property("visible"), "Escape must not dismiss a running operation"
    bridge._state = replace(bridge._state, **{state_key: state})
    bridge.changed.emit()
    QTest.qWait(50)
    QTest.keyClick(root, Qt.Key_Escape)
    QTest.qWait(300)
    assert not dialog.property("visible")
    assert warnings == []


def test_empty_preview_grid_reflows_with_available_width(popup_window):
    _, _, root, warnings = popup_window
    dialog = open_dialog(root, "EmptyFrames", (480, 600))
    grid = next(
        item
        for item in descendants(dialog.property("contentItem"))
        if item.inherits("QQuickGridLayout")
    )
    assert grid.property("columns") == 1
    root.resize(1024, 600)
    QTest.qWait(300)
    assert grid.property("columns") == 2
    assert dialog.property("loaded") == 8
    assert warnings == []


@pytest.mark.parametrize("size", SIZES)
def test_enlarged_empty_frame_dialog_fits_the_window(popup_window, size):
    _, _, root, warnings = popup_window
    open_dialog(root, "EmptyFrames", size)
    zoom = root.findChild(QObject, "enlargedEmptyFrame")
    assert QMetaObject.invokeMethod(zoom, "open")
    for _ in range(100):
        QTest.qWait(20)
        if zoom.property("opened"):
            break
    assert zoom.property("opened")
    window = QRectF(0, 0, root.width(), root.height())
    rect = popup_rect(zoom)
    assert_inside(rect, window)
    assert rect.width() <= window.width() * 0.9 + 1
    assert rect.height() <= window.height() * 0.9 + 1
    QMetaObject.invokeMethod(zoom, "close")
    assert warnings == []
