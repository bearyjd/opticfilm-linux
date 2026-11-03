"""Tray growth preserves existing delegates and all slide previews."""

import dataclasses
import os
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = pytest.mark.qt
pytest.importorskip("PySide6")

from PySide6.QtCore import QPersistentModelIndex
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from opticfilm_linux.app.prefs import MemoryPrefs
from opticfilm_linux.app.qt.bridge import Bridge
from opticfilm_linux.app.qt.models import FIRST_ROLE, SheetModel
from opticfilm_linux.app.state import EMPTY_STATE, SlideCard, TrayRow
from opticfilm_linux.batch.model import SlideSettings, SlideStatus, TrayStage


def test_newest_tray_inserts_without_reset_or_replacing_existing_rows():
    model = SheetModel(("number", "slides"))
    model.set_rows([{"number": 2, "slides": []}, {"number": 1, "slides": []}])
    first = QPersistentModelIndex(model.index(0))
    inserted = QSignalSpy(model.rowsInserted)
    reset = QSignalSpy(model.modelReset)
    changed = QSignalSpy(model.dataChanged)
    model.set_rows(
        [
            {"number": 3, "slides": []},
            {"number": 2, "slides": []},
            {"number": 1, "slides": [{"previewToken": 91}]},
        ]
    )
    assert reset.count() == 0
    assert inserted.count() == 1
    assert list(inserted.at(0)[1:]) == [0, 0]
    assert first.isValid() and first.row() == 1
    assert model.data(first, FIRST_ROLE) == 2
    assert changed.count() == 1
    assert changed.at(0)[0].row() == 2
    assert model.indexOfTray(1) == 2


def test_bridge_sheet_contains_all_trays_and_the_focused_slide():
    app = QApplication.instance() or QApplication(["test"])
    bridge = Bridge(Mock(), MemoryPrefs())
    cards = tuple(
        SlideCard(1, SlideStatus.ACCEPTED, (), SlideSettings(), True, False, False, n, n + 1)
        for n in (10, 20)
    )
    trays = tuple(
        TrayRow(n, TrayStage.REVIEW, "Review", None, None, None, 0, False, slides=(card,))
        for n, card in enumerate(cards, 1)
    )
    bridge._apply(
        dataclasses.replace(
            EMPTY_STATE,
            batch_session=1,
            trays=trays,
            selected=1,
            slides=(cards[0],),
            selection=frozenset({(2, 1)}),
            focus=(2, 1),
        )
    )
    assert bridge.sheet.rowCount() == 2
    assert bridge.sheet.get(0)["number"] == 2
    slide = bridge.sheet.get(0)["slides"][0]
    assert slide["selected"] and slide["focused"] and slide["selectable"]
    assert slide["previewToken"] == 20
    assert slide["afterUrl"].endswith("/2/1/after/20")
    assert bridge.focusedSlide["tray"] == 2
    assert bridge.sheet.get(1)["slides"][0]["previewToken"] == 10
    assert app is not None
