"""Selection slots and immutable snapshots are visible through Qt's metaobject."""

import dataclasses
import os
from unittest.mock import Mock, call

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = pytest.mark.qt
pytest.importorskip("PySide6")

from PySide6.QtCore import Q_ARG, QMetaObject, Qt
from PySide6.QtWidgets import QApplication

from opticfilm_linux.app.prefs import MemoryPrefs
from opticfilm_linux.app.qt.bridge import Bridge
from opticfilm_linux.app.state import EMPTY_STATE, SelectionSummary
from opticfilm_linux.batch.model import SlideStatus


@pytest.fixture
def bridge():
    app = QApplication.instance() or QApplication(["test"])
    controller = Mock()
    controller.tick.return_value = False
    bridge = Bridge(controller, MemoryPrefs())
    yield bridge
    assert app is not None


@pytest.mark.parametrize(
    "control,shift", [(False, False), (True, False), (False, True), (True, True)]
)
def test_qt_modifier_translation_and_metaobject_slot(bridge, control, shift):
    modifiers = 0
    if control:
        modifiers |= Qt.KeyboardModifier.ControlModifier.value
    if shift:
        modifiers |= Qt.KeyboardModifier.ShiftModifier.value
    assert QMetaObject.invokeMethod(
        bridge,
        "selectSlide",
        Qt.ConnectionType.DirectConnection,
        Q_ARG(int, 2),
        Q_ARG(int, 3),
        Q_ARG(int, modifiers),
    )
    bridge._controller.select_slide.assert_called_once_with(2, 3, control, shift)


def test_properties_notify_and_return_copies(bridge):
    bridge._state = dataclasses.replace(
        EMPTY_STATE,
        selection=frozenset({(2, 3), (1, 1)}),
        focus=(2, 3),
        selection_summary=SelectionSummary(2, 1, (("brightness_ev", None), ("rotation", 90))),
    )
    assert bridge.property("selection") == [[1, 1], [2, 3]]
    assert bridge.property("selectionFocus") == [2, 3]
    summary = bridge.property("selectionSummary")
    assert summary == {
        "count": 2,
        "accepted": 1,
        "settings": {"brightness_ev": None, "rotation": 90},
        "mixed": [],
        "trays": [],
        "meta": {},
        "metaLevels": {},
        "metaNote": "",
        "batchMeta": {},
        "metaScope": "",
    }
    summary["settings"]["rotation"] = 0
    assert bridge.selectionSummary["settings"]["rotation"] == 90
    meta = bridge.metaObject()
    for name in ("selection", "selectionFocus", "selectionSummary", "batchName"):
        prop = meta.property(meta.indexOfProperty(name))
        assert bytes(prop.notifySignal().methodSignature()) == b"changed()"


def test_unaccept_uses_pending_and_adjust_translates_fields(bridge):
    bridge.setSelectionAccepted(False)
    bridge.adjustSelection("color", 0.7)
    bridge.adjustSelection("unknown", 4.0)
    assert bridge._controller.method_calls == [
        call.set_selection_status(SlideStatus.PENDING),
        call.adjust_selection({"color_strength": 0.7}),
    ]


def test_tray_modifiers_keep_legacy_slot_and_add_range_selection(bridge):
    bridge.selectTray(2)
    modifiers = Qt.KeyboardModifier.ControlModifier.value | Qt.KeyboardModifier.ShiftModifier.value
    assert QMetaObject.invokeMethod(
        bridge,
        "selectTray",
        Qt.ConnectionType.DirectConnection,
        Q_ARG(int, 3),
        Q_ARG(int, modifiers),
    )
    assert bridge._controller.method_calls == [
        call.select_tray(2),
        call.select_tray_slides(3, True, True),
    ]


def test_filter_and_review_slots_forward_to_bulk_commands(bridge):
    bridge.selectFlagged()
    bridge.selectFailed()
    bridge.toggleSelectionAccepted()
    bridge.setSelectionStatus("rejected")
    bridge.setSelectionStatus("needs_look")
    bridge.setSelectionStatus("blank")
    bridge.reexportSelection()
    bridge.undo()
    assert bridge._controller.method_calls == [
        call.select_where("flagged"),
        call.select_where("failed"),
        call.toggle_selection_accepted(),
        call.set_selection_status(SlideStatus.PENDING),
        call.set_selection_status(SlideStatus.NEEDS_LOOK),
        call.reexport_selection(),
        call.undo(),
    ]


def test_metadata_slots_forward_blank_inherit_and_scope(bridge):
    for slot, args in (
        ("setSelectionMeta", ("description", "Lake")),
        ("clearSelectionMeta", ("date",)),
        ("resetSelectionMeta", ("date",)),
        ("setTrayMeta", (2, "date", "1978-07")),
        ("clearTrayMeta", (2, "description")),
        ("resetTrayMeta", (2, "description")),
        ("setBatchMeta", ("description", "Trip", "inheriting")),
        ("clearBatchMeta", ("date", "from_now_on")),
    ):
        assert getattr(bridge, slot)(*args) == ""
    assert bridge._controller.method_calls == [
        call.set_selection_meta("description", "Lake"),
        call.set_selection_meta("date", ""),
        call.set_selection_meta("date", None),
        call.set_tray_meta(2, "date", "1978-07"),
        call.set_tray_meta(2, "description", ""),
        call.set_tray_meta(2, "description", None),
        call.set_batch_meta("description", "Trip", "inheriting"),
        call.set_batch_meta("date", "", "from_now_on"),
    ]


def test_metadata_slot_returns_the_refusal_for_an_inline_message(bridge):
    bridge._controller.set_selection_meta.side_effect = ValueError("year must be between")
    assert bridge.setSelectionMeta("date", "1850") == "year must be between"
    meta = bridge.metaObject()
    assert meta.indexOfMethod("setBatchMeta(QString,QString,QString)") >= 0


def test_selection_summary_exposes_metadata_for_the_strip(bridge):
    bridge._state = dataclasses.replace(
        EMPTY_STATE,
        selection_summary=SelectionSummary(
            2,
            meta=(("description", None), ("date", "1978")),
            meta_levels=(("description", None), ("date", "tray")),
            meta_note="day dropped from 1978-07-14",
            batch_meta=(("description", "Trip"), ("date", "")),
        ),
    )
    summary = bridge.selectionSummary
    assert summary["meta"] == {"description": None, "date": "1978"}
    assert summary["metaLevels"] == {"description": None, "date": "tray"}
    assert summary["metaNote"] == "day dropped from 1978-07-14"
    assert summary["batchMeta"] == {"description": "Trip", "date": ""}
