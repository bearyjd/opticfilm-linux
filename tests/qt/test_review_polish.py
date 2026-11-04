"""Startup cleanliness, review floor, inspector preview floor, keyboard-only empty review."""
# ruff: noqa: F811

import os

import pytest

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
pytest.importorskip("PySide6")

from dialog_layout_probe import body_flickable
from layout_fixture import pump
from layout_probe import named, tree
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest
from test_dialog_layout import open_dialog, popup_window  # noqa: F401
from test_responsive import evaluate, layout, open_drawer  # noqa: F401

REVIEW_FLOOR = 240


def test_startup_reports_no_warnings(layout):
    assert layout.startup_warnings == ()
    assert layout.warnings == []


def test_review_area_floor_at_800x600_with_pinned_footer(layout):
    layout.apply(size=(800, 600))
    review = named(tree(layout.engine, layout.root), "reviewArea").rect
    assert not evaluate(layout, "mainPage", "shortWindow") or review.height >= 0.4 * 600 - 1
    assert review.height >= REVIEW_FLOOR, review


def test_inspector_preview_keeps_a_floor_in_a_very_short_window(layout):
    layout.apply(size=(800, 600))
    layout.bridge.selectSlide(1, 1, 0)
    pump(100)
    popup = open_drawer(layout, "inspector")
    layout.root.resize(800, 160)
    pump(350)
    nodes = tree(layout.engine, layout.root, popup)
    preview = named(nodes, "inspectorPreview")
    floor = 0.5 * evaluate(layout, "inspectorPreview", "Theme.minCell")
    assert preview.rect.height >= floor - 1, (preview.rect, floor)
    popup.invoke("close")
    pump(350)


def _enlarge(dialog, position, tail):
    """Evaluate `tail` on an Enlarge button inside QML (item wrappers are unreliable here)."""
    script = """(function find(item) {
        if (item.objectName === "enlargeEmptyFrame@POS@")
            return item;
        for (const child of item.children) {
            const found = find(child);
            if (found)
                return found;
        }
        return null;
    })({objectName: "", children: children})""".replace("@POS@", str(position))
    expression = QQmlExpression(
        QQmlEngine.contextForObject(dialog), dialog.property("contentItem"), script + tail
    )
    value, _undefined = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return value


# Scene y of the button.
IN_VIEW = ".mapToItem(null, 0, 0).y"


def _assert_visible(dialog, body, position):
    top = _enlarge(dialog, position, IN_VIEW)
    height = _enlarge(dialog, position, ".height")
    origin = body.mapToScene(QPointF(0, 0)).y()
    assert origin - 1 <= top and top + height <= origin + body.height() + 1


def test_keyboard_only_reaches_end_and_enables_confirm(popup_window):
    _, _, root, warnings = popup_window
    dialog = open_dialog(root, "EmptyFrames", (720, 540))
    confirm = root.findChild(QObject, "confirmReviewedEmptyFrames")
    body = body_flickable(dialog)
    assert body.property("contentHeight") > body.height()
    assert not confirm.property("enabled")
    for _ in range(3):
        QTest.keyClick(root, Qt.Key.Key_Tab)
        QTest.qWait(30)
        focused = next((n for n in range(1, 9) if _enlarge(dialog, n, ".activeFocus")), 0)
        if focused:
            _assert_visible(dialog, body, focused)
    for _ in range(20):
        if confirm.property("enabled"):
            break
        QTest.keyClick(root, Qt.Key.Key_PageDown)
        QTest.qWait(30)
    assert body.property("atYEnd")
    assert confirm.property("enabled")
    assert confirm.property("text") == "Confirm all these frames are empty"
    assert warnings == []


def test_focusing_an_enlarge_button_scrolls_it_into_view(popup_window):
    _, _, root, warnings = popup_window
    dialog = open_dialog(root, "EmptyFrames", (720, 540))
    body = body_flickable(dialog)
    _enlarge(dialog, 8, ".forceActiveFocus()")
    QTest.qWait(100)
    assert body.property("contentY") > 0
    _assert_visible(dialog, body, 8)
    assert warnings == []


def test_confirm_stays_disabled_until_previews_load_even_when_scrolled(popup_window):
    _, _, root, warnings = popup_window
    dialog = open_dialog(root, "EmptyFrames", (720, 540))
    confirm = root.findChild(QObject, "confirmReviewedEmptyFrames")
    dialog.setProperty("loaded", 7)
    for _ in range(10):
        QTest.keyClick(root, Qt.Key.Key_PageDown)
        QTest.qWait(30)
    assert body_flickable(dialog).property("atYEnd")
    assert not confirm.property("enabled")
    assert warnings == []
