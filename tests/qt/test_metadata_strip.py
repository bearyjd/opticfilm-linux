"""The metadata strip edits the description and estimated date of the selection, a tray or
the batch default, with real key presses, and never triggers the review shortcuts."""
# ruff: noqa: F811

import dataclasses
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = pytest.mark.qt
pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, Qt, QUrl
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from test_contact_sheet_ui import _tap, _visual
from test_qt_smoke import _wait, loaded, qapp  # noqa: F401

from opticfilm_linux.batch.metadata import MetaOverride
from opticfilm_linux.batch.model import SlideRecord, SlideStatus, TrayRecord, TrayStage
from opticfilm_linux.batch.runner import BatchRunner


@pytest.fixture
def strip(loaded, tmp_path, monkeypatch):
    bridge, engine, warnings = loaded
    monkeypatch.setattr(BatchRunner, "_queue_stale_previews", lambda self: None)
    bridge.newBatch(QUrl.fromLocalFile(str(tmp_path)).toString(), "Strip", "", "", 600)
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
    focus_review(root)
    yield bridge, root, store
    assert warnings == []


def focus_review(root):
    root.findChild(QQuickItem, "mainPage").forceActiveFocus()
    QTest.qWait(50)


def find(root, name):
    item = root.findChild(QObject, name)
    assert item is not None, name
    return item


def slides(store):
    return [s for tray in store.snapshot().trays for s in tray.slides]


def type_into(root, name, text, enter=True):
    item = find(root, name)
    item.forceActiveFocus(Qt.FocusReason.TabFocusReason)
    QTest.qWait(20)
    for char in text:
        key = Qt.Key(ord(char.upper()))  # the real key code, as the review shortcuts see it
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QGuiApplication.sendEvent(root, QKeyEvent(kind, key, Qt.KeyboardModifier.NoModifier, char))
    if enter:
        QTest.keyClick(root, Qt.Key.Key_Return)
    QCoreApplication.processEvents()


def click(root, name):
    item = find(root, name)
    assert item.property("enabled"), name
    assert QMetaObject.invokeMethod(item, "click")
    QCoreApplication.processEvents()


def select_all(bridge, root):
    focus_review(root)
    QTest.keyClick(root, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    _wait(bridge, lambda: len(bridge.selection) == 8)


def test_typed_description_and_enter_change_every_selected_slide(strip):
    bridge, root, store = strip
    assert find(root, "metadataStrip").property("visible")
    select_all(bridge, root)
    _wait(bridge, lambda: find(root, "metaDescription").property("enabled"))
    type_into(root, "metaDescription", "Lake trip")
    _wait(bridge, lambda: all(s.meta.description == "Lake trip" for s in slides(store)))
    _wait(bridge, lambda: find(root, "metaLevelDescription").property("text") == "edited")
    assert all(s.meta.date is None for s in slides(store))  # the date was left alone


def test_a_mixed_selection_shows_mixed_and_tabbing_through_changes_nothing(strip):
    bridge, root, store = strip
    store.update_tray(
        1,
        slides=(
            SlideRecord(1, meta=MetaOverride(description="own")),
            *(SlideRecord(p) for p in range(2, 5)),
        ),
    )
    bridge._controller._rebuild()
    select_all(bridge, root)
    field = find(root, "metaDescription")
    _wait(bridge, lambda: field.property("placeholderText") == "(mixed)")
    assert field.property("text") == ""
    assert find(root, "metaLevelDescription").property("text") == "(mixed)"
    field.forceActiveFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(root, Qt.Key.Key_Tab)
    QTest.qWait(100)
    assert [s.meta for s in slides(store)][0] == MetaOverride(description="own")
    assert all(s.meta == MetaOverride() for s in slides(store)[1:])


def test_clear_and_reset_to_inherited_differ(strip):
    bridge, root, store = strip
    store.update(lambda m: dataclasses.replace(m, description="Batch", date="1978"))
    bridge._controller._rebuild()
    _tap(root, "slideThumb_2_1")
    _wait(bridge, lambda: bridge.selection == [[2, 1]])
    _wait(bridge, lambda: find(root, "metaDescription").property("text") == "Batch")
    assert find(root, "metaLevelDescription").property("text") == "from batch"
    click(root, "metaClear")
    first = lambda: store.snapshot().trays[1].slides[0].meta  # noqa: E731
    _wait(bridge, lambda: first() == MetaOverride(description="", date=""))
    _wait(bridge, lambda: find(root, "metaReset").property("enabled"))
    click(root, "metaReset")
    _wait(bridge, lambda: first() == MetaOverride())


def test_ctrl_z_undoes_a_description_then_a_rotate_in_order(strip):
    bridge, root, store = strip
    _tap(root, "slideThumb_1_1")
    _wait(bridge, lambda: bridge.selection == [[1, 1]])
    focus_review(root)
    QTest.keyClick(root, Qt.Key.Key_BracketRight)
    first = lambda: store.snapshot().trays[0].slides[0]  # noqa: E731
    _wait(bridge, lambda: first().settings.rotation == 90)
    type_into(root, "metaDescription", "Picnic")
    _wait(bridge, lambda: first().meta.description == "Picnic")
    page = find(root, "mainPage")
    _wait(bridge, lambda: page.property("activeFocus"))  # Enter hands the keys back
    QTest.keyClick(root, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    _wait(bridge, lambda: first().meta.description is None)
    assert first().settings.rotation == 90
    QTest.keyClick(root, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    _wait(bridge, lambda: first().settings.rotation == 0)


def test_an_invalid_year_shows_the_inline_error_and_changes_nothing(strip):
    bridge, root, store = strip
    select_all(bridge, root)
    type_into(root, "metaYear", "1850")
    error = find(root, "metaError")
    _wait(bridge, lambda: error.property("visible"))
    assert "1900" in error.property("text")
    QTest.qWait(100)
    assert all(s.meta == MetaOverride() for s in slides(store))
    find(root, "metaYear").setProperty("text", "")
    type_into(root, "metaYear", "1978")
    _wait(bridge, lambda: all(s.meta.date == "1978" for s in slides(store)))
    _wait(bridge, lambda: not error.property("visible"))


def test_review_keys_typed_into_the_strip_do_not_act_on_slides(strip):
    bridge, root, store = strip
    select_all(bridge, root)
    type_into(root, "metaDescription", "a[] rn m", enter=False)
    assert find(root, "metaDescription").property("text") == "a[] rn m"
    QTest.keyClick(root, Qt.Key.Key_Escape)  # Escape drops the typing and leaves the strip
    _wait(bridge, lambda: find(root, "mainPage").property("activeFocus"))
    assert find(root, "metaDescription").property("text") == ""
    find(root, "metaYear").setProperty("text", "1978")
    find(root, "metaMonth").forceActiveFocus(Qt.FocusReason.TabFocusReason)
    for key in (Qt.Key.Key_A, Qt.Key.Key_BracketRight, Qt.Key.Key_BracketLeft, Qt.Key.Key_M):
        QTest.keyClick(root, key)
    QTest.qWait(150)
    assert all(s.status is SlideStatus.PENDING for s in slides(store))
    assert all(s.settings.rotation == 0 and not s.settings.mirror for s in slides(store))
    # (a letter in the month box picks a month: that is the box's own search, not a shortcut)
    QTest.keyClick(root, Qt.Key.Key_Escape)
    _wait(bridge, lambda: find(root, "mainPage").property("activeFocus"))
    assert all(s.meta.description is None for s in slides(store))


def test_tray_dialog_sets_the_tray_meta(strip):
    bridge, root, store = strip
    store.update(lambda m: dataclasses.replace(m, description="Batch"))
    bridge._controller._rebuild()
    _wait(bridge, lambda: bridge.selectionSummary["batchMeta"]["description"] == "Batch")
    menu_item = find(root, "editTrayMeta")
    find(root, "sheetView").setProperty("menuTray", 2)
    assert QMetaObject.invokeMethod(menu_item, "click")
    dialog = find(root, "trayMetaDialog")
    _wait(bridge, lambda: dialog.property("opened"))
    _wait(bridge, lambda: find(root, "trayMetaDescription").property("text") == "Batch")
    find(root, "trayMetaDescription").setProperty("text", "Carousel 2")
    find(root, "trayMetaYear").setProperty("text", "1981")
    find(root, "trayMetaMonth").setProperty("currentIndex", 6)
    click(root, "trayMetaSave")
    tray = lambda: store.snapshot().trays[1]  # noqa: E731
    _wait(bridge, lambda: tray().meta == MetaOverride(description="Carousel 2", date="1981-06"))
    assert store.snapshot().trays[0].meta == MetaOverride()
    _wait(bridge, lambda: not dialog.property("visible"))


def test_batch_mode_from_now_on_pins_the_trays_that_still_inherit(strip):
    bridge, root, store = strip
    store.update(lambda m: dataclasses.replace(m, description="Old", date="1978"))
    bridge._controller._rebuild()
    bridge.clearSelection()
    scope = find(root, "metaBatchScope")
    _wait(bridge, lambda: scope.property("visible"))
    assert scope.property("currentValue") == "from_now_on"
    assert find(root, "metaLevelDescription").property("text") == "batch default"
    assert not find(root, "metaReset").property("visible")  # nothing above the batch
    type_into(root, "metaDescription", "New event")
    _wait(bridge, lambda: store.snapshot().description == "New event")
    assert all(t.meta.description == "Old" for t in store.snapshot().trays)
    next_tray = find(root, "nextTrayMeta")
    _wait(bridge, lambda: next_tray.property("text") == "Next tray: New event · ~1978")


def test_the_next_tray_edit_link_focuses_the_batch_mode(strip):
    bridge, root, store = strip
    _tap(root, "slideThumb_1_1")
    _wait(bridge, lambda: bridge.selection == [[1, 1]])
    assert find(root, "nextTrayMeta").property("text") == "Next tray: no description · no date"
    click(root, "nextTrayMetaEdit")
    _wait(bridge, lambda: not bridge.selection)
    _wait(bridge, lambda: find(root, "metaDescription").property("activeFocus"))
    assert find(root, "metadataStrip").property("scope") == "batch"
