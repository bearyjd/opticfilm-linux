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
