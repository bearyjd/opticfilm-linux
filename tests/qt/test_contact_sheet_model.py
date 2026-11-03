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
    assert changed.count() == 0
    child = model.data(model.index(2), FIRST_ROLE + 1)
    assert child.data(child.index(0), FIRST_ROLE)["previewToken"] == 91
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


def test_child_model_survives_selection_preview_and_placeholder_updates():
    model = SheetModel(("number", "slides", "stage", "stageText"))
    row = {"number": 1, "slides": [], "stage": "scanning", "stageText": "Scanning"}
    model.set_rows([row])
    child = model.data(model.index(0), FIRST_ROLE + 1)
    assert child.rowCount() == 4
    reset = QSignalSpy(child.modelReset)
    changed = QSignalSpy(child.dataChanged)
    slides = [dict(child.data(child.index(i), FIRST_ROLE)) for i in range(4)]
    slides[0].update(selected=True, focused=True, afterUrl="image://preview/1/1/after/2")
    model.set_rows([{**row, "slides": slides, "stage": "review"}])
    assert model.data(model.index(0), FIRST_ROLE + 1) is child
    assert reset.count() == 0 and changed.count() == 1
    assert child.data(child.index(0), FIRST_ROLE)["selected"]
    assert model.get(0)["slides"] == slides


def test_child_remains_available_during_tray_removal_notification():
    model = SheetModel(("number", "slides"))
    model.set_rows([{"number": 1, "slides": []}])
    child = model.data(model.index(0), FIRST_ROLE + 1)
    observed = []
    model.rowsAboutToBeRemoved.connect(
        lambda *_: observed.append(model.data(model.index(0), FIRST_ROLE + 1))
    )
    model.set_rows([])
    assert observed == [child]
    assert model.rowCount() == 0
