"""Selection and preview changes keep contact-sheet thumbnail objects alive."""

# ruff: noqa: F811

import dataclasses
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = pytest.mark.qt
pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from shiboken6 import getCppPointer, isValid
from test_qt_smoke import _scanned, _wait, loaded, qapp  # noqa: F401, F811

_VISUAL_REFERENCES = []


def _walk(item):
    pending = [item]
    visited = set()
    while pending:
        current = pending.pop()
        if not isValid(current):
            continue
        pointer = getCppPointer(current)[0]
        if pointer in visited:
            continue
        visited.add(pointer)
        _VISUAL_REFERENCES.append(current)
        yield current
        children = current.childItems()
        _VISUAL_REFERENCES.extend(children)
        pending.extend(children)


def _find(item, name):
    return next((child for child in _walk(item) if child.objectName() == name), None)


def _images(item):
    return [child for child in _walk(item) if child.inherits("QQuickImage")]


def test_click_arrow_and_preview_update_preserve_thumbnails(loaded, tmp_path):
    bridge, engine, warnings = loaded
    _scanned(bridge, tmp_path, "Identity")
    root = engine.rootObjects()[0]
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    sheet = root.findChild(QQuickItem, "sheetView")
    assert sheet is not None
    _wait(bridge, lambda: _find(sheet, "slideThumb_1_1") is not None)
    thumbs = [_find(sheet, f"slideThumb_1_{p}") for p in range(1, 5)]
    _wait(bridge, lambda: thumbs[0].property("slide")["selectable"])
    QTest.qWait(100)
    images = [_images(thumb)[0] for thumb in thumbs]
    point = thumbs[0].mapToScene(QPointF(thumbs[0].width() / 2, thumbs[0].height() / 2))
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, QPoint(round(point.x()), round(point.y())))
    _wait(bridge, lambda: bridge.selectionFocus == [1, 1])
    QTest.keyClick(root, Qt.Key_Right)
    _wait(bridge, lambda: bridge.selectionFocus == [1, 2])
    for position, (thumb, image) in enumerate(zip(thumbs, images, strict=True), 1):
        assert isValid(thumb) and isValid(image)
        assert _find(sheet, f"slideThumb_1_{position}") is thumb
        assert _images(thumb)[0] is image
    assert thumbs[1].property("slide")["focused"]
    state = bridge._state
    tray = state.trays[0]
    card = dataclasses.replace(tray.slides[0], preview_token=9876)
    updated = dataclasses.replace(tray, slides=(card, *tray.slides[1:]))
    bridge._apply(dataclasses.replace(state, trays=(updated, *state.trays[1:])))
    QTest.qWait(20)
    assert isValid(images[0]) and _images(thumbs[0])[0] is images[0]
    assert images[0].property("source").toString().endswith("/after/9876")
    assert not [
        warning for warning in warnings if "ReferenceError" in warning or "TypeError" in warning
    ]
