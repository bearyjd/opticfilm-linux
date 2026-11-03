"""Contact sheet gestures and Inspector edits reach the real selection controller."""
# ruff: noqa: F811

import dataclasses
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = pytest.mark.qt
pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QMetaObject, QObject, QPointF, Qt, QUrl
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from shiboken6 import isValid
from test_qt_smoke import _wait, loaded, qapp  # noqa: F401

from opticfilm_linux.batch.model import (
    Flag,
    SlideRecord,
    SlideSettings,
    SlideStatus,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.runner import BatchRunner

_VISUAL_REFERENCES = []


@pytest.fixture
def sheet_window(loaded, tmp_path, monkeypatch):
    bridge, engine, warnings = loaded
    # These tests edit metadata; preview rendering has separate integration coverage.
    monkeypatch.setattr(BatchRunner, "_queue_stale_previews", lambda self: None)
    bridge.newBatch(QUrl.fromLocalFile(str(tmp_path)).toString(), "Sheet", "", "", 600)
    _wait(bridge, lambda: bridge.hasBatch)
    store = bridge._controller._session.store
    trays = tuple(
        TrayRecord(number, TrayStage.REVIEW, slides=tuple(SlideRecord(p) for p in range(1, 5)))
        for number in (1, 2)
    )
    store.update(lambda m: dataclasses.replace(m, trays=trays))
    bridge._controller._rebuild()
    _wait(bridge, lambda: bridge.sheet.rowCount() == 2)
    root = engine.rootObjects()[0]
    root.setHeight(1100)
    root.setWidth(1500)
    _wait(bridge, lambda: _visual(root, "slideThumb_1_4") is not None)
    page = root.findChild(QQuickItem, "mainPage")
    page.forceActiveFocus()
    QTest.qWait(100)
    yield bridge, root, store, warnings
    assert warnings == []


def _visual(root, name):
    if root.objectName() == name:
        return root
    direct = root.findChild(QQuickItem, name)
    if direct is not None and isValid(direct):
        _VISUAL_REFERENCES.append(direct)
        return direct
    if not isinstance(root, QQuickItem):
        return _visual(root.findChild(QQuickItem, "sheetView"), name)
    children = root.childItems()
    _VISUAL_REFERENCES.extend(children)
    for child in children:
        found = _visual(child, name)
        if found is not None:
            return found
    return None


def _tap(root, name, modifiers=Qt.KeyboardModifier.NoModifier, x_fraction=0.5):
    QTest.qWait(50)
    item = _visual(root, name)
    assert item is not None and item.isVisible(), name
    point = item.mapToScene(QPointF(item.width() * x_fraction, item.height() / 2))
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, modifiers, point.toPoint())
    QCoreApplication.processEvents()


def _statuses(store):
    return [s.status for tray in store.snapshot().trays for s in tray.slides]


def test_click_shift_ctrl_and_keyboard_selection_across_trays(sheet_window):
    bridge, root, _store, _warnings = sheet_window
    _tap(root, "slideThumb_2_1")
    _wait(bridge, lambda: bridge.selection == [[2, 1]])
    _tap(root, "slideThumb_1_4", Qt.KeyboardModifier.ShiftModifier)
    _wait(bridge, lambda: len(bridge.selection) == 8)
    _tap(root, "slideThumb_2_2", Qt.KeyboardModifier.ControlModifier)
    _wait(bridge, lambda: len(bridge.selection) == 7)
    assert [2, 2] not in bridge.selection
    QTest.keyClick(root, Qt.Key.Key_Escape)
    _wait(bridge, lambda: not bridge.selection)
    QTest.keyClick(root, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    _wait(bridge, lambda: len(bridge.selection) == 8)
    _tap(root, "slideThumb_2_4")
    QTest.keyClick(root, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    _wait(bridge, lambda: bridge.selection == [[1, 1], [2, 4]])
    assert bridge.selectionFocus == [1, 1]
    QTest.keyClick(root, Qt.Key.Key_Up)
    _wait(bridge, lambda: bridge.selectionFocus == [2, 1])
    sheet = _visual(root, "sheetView")
    point = sheet.mapToScene(QPointF(sheet.width() / 2, sheet.height() - 8))
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, pos=point.toPoint())
    _wait(bridge, lambda: not bridge.selection)


def test_bulk_inspector_mixed_slider_and_review_keys(sheet_window):
    bridge, root, store, _warnings = sheet_window
    store.update_tray(
        1,
        slides=(
            SlideRecord(1, settings=SlideSettings(brightness_ev=1)),
            *(SlideRecord(p) for p in range(2, 5)),
        ),
    )
    bridge._controller._rebuild()
    bridge.selectAll()
    _wait(bridge, lambda: len(bridge.selection) == 8)
    label = root.findChild(QObject, "brightnessValue")
    _wait(bridge, lambda: "mixed" in label.property("text"))
    _tap(root, "brightnessSlider", x_fraction=0.8)
    _wait(
        bridge,
        lambda: (
            len({s.settings.brightness_ev for t in store.snapshot().trays for s in t.slides}) == 1
        ),
    )
    _wait(bridge, lambda: "mixed" not in label.property("text"))
    root.findChild(QQuickItem, "mainPage").forceActiveFocus()
    QTest.keyClick(root, Qt.Key.Key_Space)
    _wait(bridge, lambda: all(s is SlideStatus.ACCEPTED for s in _statuses(store)))
    QTest.keyClick(root, Qt.Key.Key_R)
    _wait(bridge, lambda: all(s is SlideStatus.PENDING for s in _statuses(store)))
    QTest.keyClick(root, Qt.Key.Key_N)
    _wait(bridge, lambda: all(s is SlideStatus.NEEDS_LOOK for s in _statuses(store)))
    QTest.keyClick(root, Qt.Key.Key_A)
    _wait(bridge, lambda: all(s is SlideStatus.ACCEPTED for s in _statuses(store)))


def test_inspector_preview_segments_and_tray_selection(sheet_window):
    bridge, root, _store, _warnings = sheet_window
    _tap(root, "sheetTray_2")
    _wait(bridge, lambda: len(bridge.selection) == 4)
    panel = root.findChild(QObject, "inspectorPanel")
    assert panel.property("singleTray") == 2
    mode = root.findChild(QObject, "previewMode")
    segment = _visual(mode, "segment0")
    assert QMetaObject.invokeMethod(segment, "click")
    assert panel.property("showBefore")
    assert QMetaObject.invokeMethod(_visual(mode, "segment1"), "click")
    assert not panel.property("showBefore")
    _tap(root, "sheetTray_1", Qt.KeyboardModifier.ControlModifier)
    _wait(bridge, lambda: len(bridge.selection) == 8)
    assert panel.property("singleTray") == -1


def test_reexport_confirmation_can_cancel_and_dispatches_once(sheet_window, monkeypatch):
    from unittest.mock import Mock

    bridge, root, _store, _warnings = sheet_window
    bridge.selectAll()
    _wait(bridge, lambda: len(bridge.selection) == 8)
    dispatch = Mock()
    monkeypatch.setattr(bridge._controller, "reexport_selection", dispatch)
    button = root.findChild(QObject, "reexportSelection")
    assert QMetaObject.invokeMethod(button, "click")
    dialog = root.findChild(QObject, "reexportSelectionDialog")
    _wait(bridge, lambda: dialog.property("opened"))
    assert "new numbered files" in dialog.property("subtitle")
    dispatch.assert_not_called()
    assert QMetaObject.invokeMethod(dialog, "reject")
    dispatch.assert_not_called()
    assert QMetaObject.invokeMethod(button, "click")
    _wait(bridge, lambda: dialog.property("opened"))
    assert QMetaObject.invokeMethod(dialog, "accept")
    dispatch.assert_called_once_with()


def test_sidebar_filters_scroll_to_old_trays_and_keep_review_keys(sheet_window):
    bridge, root, store, _warnings = sheet_window
    trays = tuple(
        TrayRecord(
            n,
            TrayStage.REVIEW,
            slides=tuple(
                SlideRecord(p, flags=(Flag.DUST,) if (n, p) == (1, 1) else ()) for p in range(1, 5)
            ),
            auto_failed=(2,) if n == 2 else (),
        )
        for n in range(1, 13)
    )
    store.update(lambda m: dataclasses.replace(m, trays=trays))
    bridge._controller._rebuild()
    _wait(bridge, lambda: bridge.sheet.rowCount() == 12)
    assert _visual(root, "selectFlagged").property("text") == "Select flagged (1)"
    assert _visual(root, "selectFailed").property("text") == "Select failed (1)"
    _tap(root, "selectFlagged")
    _wait(bridge, lambda: bridge.selection == [[1, 1]])
    sheet = _visual(root, "sheetView")
    _wait(bridge, lambda: sheet.property("contentY") > 1000)
    _wait(bridge, lambda: root.activeFocusItem().objectName() == "mainPage")
    QTest.keyClick(root, Qt.Key.Key_Space)
    _wait(bridge, lambda: store.snapshot().trays[0].slides[0].status is SlideStatus.ACCEPTED)
    _tap(root, "selectFailed")
    _wait(bridge, lambda: bridge.selection == [[2, 2]])
    assert bridge.selectionFocus == [2, 2]
    assert sheet.property("contentY") > 1000


def test_slider_drag_commits_once_on_release_and_undo_restores_start(sheet_window):
    bridge, root, store, _warnings = sheet_window
    bridge.selectAll()
    _wait(bridge, lambda: len(bridge.selection) == 8)
    slider = _visual(root, "brightnessSlider")
    start = slider.mapToScene(QPointF(slider.width() / 2, slider.height() / 2)).toPoint()
    QTest.mousePress(root, Qt.MouseButton.LeftButton, pos=start)
    for fraction in (0.6, 0.7, 0.8):
        end = slider.mapToScene(QPointF(slider.width() * fraction, slider.height() / 2))
        QTest.mouseMove(root, end.toPoint(), delay=30)
        QCoreApplication.processEvents()
        bridge.poll()
    assert not bridge.undoLabel
    assert all(s.settings.brightness_ev == 0 for t in store.snapshot().trays for s in t.slides)
    QTest.mouseRelease(root, Qt.MouseButton.LeftButton, pos=end.toPoint())
    _wait(bridge, lambda: bool(bridge.undoLabel))
    assert len(bridge._controller._undo) == 1
    assert all(s.settings.brightness_ev > 0 for t in store.snapshot().trays for s in t.slides)
    bridge.undo()
    _wait(bridge, lambda: not bridge.undoLabel)
    assert all(s.settings.brightness_ev == 0 for t in store.snapshot().trays for s in t.slides)


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_bare_enter_does_not_dispatch_hardware_actions(sheet_window, monkeypatch, key):
    from unittest.mock import Mock

    bridge, root, _store, _warnings = sheet_window
    _wait(bridge, lambda: bridge.canScan)
    dispatch = Mock()
    monkeypatch.setattr(bridge._controller, "scan", dispatch)
    root.findChild(QQuickItem, "mainPage").forceActiveFocus()
    QTest.keyClick(root, key)
    QCoreApplication.processEvents()
    dispatch.assert_not_called()
    assert QMetaObject.invokeMethod(root.findChild(QObject, "primaryActionButton"), "click")
    dispatch.assert_called_once_with()


def test_mirror_checked_state_tracks_model_after_click_and_undo(sheet_window):
    bridge, root, _store, _warnings = sheet_window
    bridge.selectAll()
    _wait(bridge, lambda: len(bridge.selection) == 8)
    button = root.findChild(QObject, "mirrorSelection")
    assert not button.property("checked")
    assert QMetaObject.invokeMethod(button, "click")
    _wait(bridge, lambda: bridge.selectionSummary["settings"]["mirror"] is True)
    assert button.property("checked")
    bridge.undo()
    _wait(bridge, lambda: bridge.selectionSummary["settings"]["mirror"] is False)
    assert not button.property("checked")


def test_mirror_does_not_toggle_before_model_confirms_edit(sheet_window, monkeypatch):
    from unittest.mock import Mock

    bridge, root, _store, _warnings = sheet_window
    bridge.selectAll()
    _wait(bridge, lambda: len(bridge.selection) == 8)
    dispatch = Mock()
    monkeypatch.setattr(bridge._controller, "mirror_selection", dispatch)
    button = root.findChild(QObject, "mirrorSelection")
    assert QMetaObject.invokeMethod(button, "click")
    dispatch.assert_called_once_with()
    assert not button.property("checked")


def test_slider_keyboard_arrow_commits_without_pointer_release(sheet_window):
    bridge, root, store, _warnings = sheet_window
    bridge.selectAll()
    _wait(bridge, lambda: len(bridge.selection) == 8)
    slider = _visual(root, "brightnessSlider")
    slider.forceActiveFocus()
    QTest.keyClick(root, Qt.Key.Key_Right)
    _wait(bridge, lambda: bool(bridge.undoLabel))
    assert len(bridge._controller._undo) == 1
    assert all(s.settings.brightness_ev > 0 for t in store.snapshot().trays for s in t.slides)


def test_empty_failed_tray_header_enables_rescan_confirmation(sheet_window):
    bridge, root, store, _warnings = sheet_window
    store.update(lambda m: dataclasses.replace(m, trays=(TrayRecord(1, TrayStage.FAILED),)))
    bridge._controller._rebuild()
    _wait(bridge, lambda: bridge.sheet.rowCount() == 1 and bridge.canScan)
    _tap(root, "sheetTray_1")
    panel = root.findChild(QObject, "inspectorPanel")
    _wait(bridge, lambda: panel.property("singleTray") == 1)
    assert panel.property("count") == 0
    button = root.findChild(QQuickItem, "rescanSelectionTray")
    assert button.isEnabled()
    _tap(root, "rescanSelectionTray")
    dialog = root.findChild(QObject, "rescanSelectionDialog")
    _wait(bridge, lambda: dialog.property("opened"))
    assert "tray 1" in dialog.property("title")
    assert "old scan is kept on disk" in dialog.property("subtitle")
    assert QMetaObject.invokeMethod(dialog, "reject")


@pytest.mark.parametrize("key", [Qt.Key.Key_Space, Qt.Key.Key_Return])
def test_tray_header_keyboard_control_modifier_keeps_existing_selection(sheet_window, key):
    bridge, root, _store, _warnings = sheet_window
    _tap(root, "sheetTray_1")
    _wait(bridge, lambda: bridge.selection == [[1, p] for p in range(1, 5)])
    header = _visual(root, "sheetTray_2")
    header.forceActiveFocus()
    QTest.keyClick(root, key, Qt.KeyboardModifier.ControlModifier)
    _wait(bridge, lambda: len(bridge.selection) == 8)
    QTest.qWait(100)
    bridge.poll()
    assert bridge.selection == [[n, p] for n in (1, 2) for p in range(1, 5)]
