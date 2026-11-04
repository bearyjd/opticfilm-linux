"""Evidence must be visible before confirmation, and long commands remain readable."""
# ruff: noqa: F811

import pytest

pytest.importorskip("PySide6")

from dialog_layout_probe import body_flickable, descendants, scene_rect
from PySide6.QtCore import QMetaObject, QObject
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest
from test_dialog_layout import footer_buttons, open_dialog, popup_window  # noqa: F401

pytestmark = pytest.mark.qt


def _grid(dialog):
    return next(
        item
        for item in descendants(dialog.property("contentItem"))
        if item.inherits("QQuickGridLayout")
    )


def _evaluate(dialog, script):
    expression = QQmlExpression(QQmlEngine.contextForObject(dialog), dialog, script)
    value, _undefined = expression.evaluate()
    assert not expression.hasError()
    return value


def _prompt_title_offset(dialog):
    return _evaluate(
        dialog,
        """
        (function() {
            function find(item, text) {
                if (item.text === text)
                    return item;
                for (const child of item.children) {
                    const result = find(child, text);
                    if (result)
                        return result;
                }
                return null;
            }
            const heading = find(contentItem, title);
            const body = find(contentItem, subtitle);
            return heading.mapToItem(body, 0, 0).x;
        })()
    """,
    )


def test_short_empty_review_requires_scrolling_and_resets_on_reopen(popup_window):
    _, _, root, warnings = popup_window
    dialog = open_dialog(root, "EmptyFrames", (720, 540))
    confirm = root.findChild(QObject, "confirmReviewedEmptyFrames")
    body = body_flickable(dialog)
    assert dialog.property("loaded") == 8
    assert body.property("contentHeight") > body.height()
    assert not confirm.property("enabled")
    body.setProperty("contentY", body.property("contentHeight") - body.height())
    QTest.qWait(50)
    assert confirm.property("enabled")
    dialog.setProperty("loaded", 7)
    assert not confirm.property("enabled")
    dialog.setProperty("loaded", 8)
    QMetaObject.invokeMethod(dialog, "close")
    QTest.qWait(300)
    open_dialog(root, "EmptyFrames", (720, 540))
    assert not confirm.property("enabled")
    assert warnings == []


def test_four_empty_previews_fit_short_window_without_scroll(popup_window):
    _, _, root, warnings = popup_window
    dialog = root.findChild(QObject, "emptyFramesDialog")
    dialog.setProperty("previews", dialog.property("previews")[:4])
    open_dialog(root, "EmptyFrames", (800, 600))
    QTest.qWait(50)
    assert _grid(dialog).property("columns") == 2
    assert dialog.property("framesFullyVisible")
    assert root.findChild(QObject, "confirmReviewedEmptyFrames").property("enabled")
    assert warnings == []


@pytest.mark.parametrize("size,columns", [((480, 600), 1), ((800, 600), 2), ((1280, 800), 4)])
def test_empty_grid_uses_available_width(popup_window, size, columns):
    _, _, root, warnings = popup_window
    dialog = open_dialog(root, "EmptyFrames", size)
    assert _grid(dialog).property("columns") == columns
    assert warnings == []


@pytest.mark.parametrize("name", ["EmptyFrames", "LoadHelp"])
def test_dialog_title_scrollbar_and_footer_placement(popup_window, name):
    _, _, root, warnings = popup_window
    dialog = open_dialog(root, name)
    title_x = _evaluate(dialog, "header.contentItem.children[0].mapToItem(contentItem, 0, 0).x")
    assert abs(title_x) <= 1
    policy = "contentItem.QQC2.ScrollBar.vertical.policy === QQC2.ScrollBar.AlwaysOn"
    assert _evaluate(dialog, policy)
    buttons = footer_buttons(root, dialog)
    if name == "EmptyFrames":
        cancel = next(button for button in buttons if button.property("text") == "Cancel")
        confirm = root.findChild(QObject, "confirmReviewedEmptyFrames")
        assert scene_rect(cancel).left() < scene_rect(confirm).left()
        assert confirm.property("text") == "Confirm all these frames are empty"
    assert warnings == []


def test_terminal_command_wraps_from_its_start(popup_window):
    _, _, root, warnings = popup_window
    dialog = open_dialog(root, "LoadHelp", (720, 540))
    command = root.findChild(QObject, "loadCommandField")
    text = "python3 /a/very/long/path/" + "scanner-directory/" * 35 + "of135i load"
    command.setProperty("text", text)
    QTest.qWait(100)
    assert command.property("text") == text
    assert command.property("lineCount") > 1
    assert command.property("contentWidth") <= command.width()
    assert command.property("contentHeight") <= command.height()
    assert root.findChild(QObject, "copyLoadCommand").property("enabled")
    policy = "contentItem.QQC2.ScrollBar.vertical.policy === QQC2.ScrollBar.AlwaysOn"
    assert _evaluate(dialog, policy)
    assert warnings == []


@pytest.mark.parametrize(
    "name", ["Export", "FreeSpace", "Orientation", "Confirm", "EmptyFrames", "LoadHelp"]
)
def test_every_dialog_title_aligns_with_body_text(popup_window, name):
    _, _, root, warnings = popup_window
    dialog = open_dialog(root, name)
    if name == "Confirm":
        assert abs(_prompt_title_offset(dialog)) <= 1
    else:
        title_x = _evaluate(dialog, "header.contentItem.children[0].mapToItem(contentItem, 0, 0).x")
        assert abs(title_x) <= 1
    assert warnings == []


def test_enlarged_frame_title_aligns_with_image(popup_window):
    _, _, root, warnings = popup_window
    open_dialog(root, "EmptyFrames")
    zoom = root.findChild(QObject, "enlargedEmptyFrame")
    assert QMetaObject.invokeMethod(zoom, "open")
    QTest.qWait(300)
    assert zoom.property("opened")
    title_x = _evaluate(zoom, "header.mapToItem(contentItem, header.leftPadding, 0).x")
    assert abs(title_x) <= 1
    assert warnings == []
