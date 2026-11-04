"""Review actions retain prominence, usable proportions, and destructive cues."""
# ruff: noqa: F811

import json
import os

import pytest

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
pytest.importorskip("PySide6")

from layout_fixture import pump
from layout_probe import named, tree
from PySide6.QtGui import QColor
from test_qt_theme import _contrast
from test_responsive import evaluate, layout, open_drawer  # noqa: F401


def _buttons(dialog):
    script = """JSON.stringify((function walk(item) {
        if (item.children.some(child => child.objectName === 'pillFocusRing')) {
            var p = item.mapToItem(null, 0, 0);
            return [{text: item.text.replace(/&/g, ''), flat: item.flat,
                     background: item.background.color.toString(),
                     label: item.labelColor.toString(), x: p.x, y: p.y}];
        }
        var result = [];
        for (var child of item.children)
            if (child.visible) result = result.concat(walk(child));
        return result;
    })(target.footer))"""
    return json.loads(dialog.evaluate(script))


@pytest.mark.parametrize("size", ((1024, 600), (1920, 1080), (3840, 2160)))
def test_inspector_acceptance_precedes_bounded_preview(layout, size):
    layout.apply(size=size)
    layout.bridge.selectSlide(1, 1, 0)
    pump(100)
    nodes = tree(layout.engine, layout.root)
    popup = None
    if not any(node.name == "inspectorPanel" for node in nodes):
        popup = open_drawer(layout, "inspector")
        nodes = tree(layout.engine, layout.root, popup)
    panel = named(nodes, "inspectorPanel")
    preview = named(nodes, "inspectorPreview")
    assert preview.rect.height <= panel.rect.height * 0.3 + 1
    for name in ("acceptSelection", "unacceptSelection"):
        action = named(nodes, name)
        assert action.rect.y + action.rect.height <= preview.rect.y
        assert panel.rect.contains(action.rect)
    if popup:
        popup.invoke("close")
        pump(350)


def test_four_slide_tray_stays_centered_at_4k(layout):
    layout.apply(size=(3840, 2160))
    nodes = tree(layout.engine, layout.root)
    sheet = named(nodes, "sheetView")
    first = named(nodes, "slideThumb_4_1")
    last = named(nodes, "slideThumb_4_4")
    assert first.rect.y == pytest.approx(last.rect.y)
    middle = (first.rect.x + last.rect.x + last.rect.width) / 2
    assert middle == pytest.approx(sheet.rect.x + sheet.rect.width / 2, abs=1)


@pytest.mark.parametrize(
    "name,affirmative", (("free-space", "Remove raw scans"), ("confirm-cleanup", "Yes"))
)
@pytest.mark.parametrize("theme", ("dark", "light"))
def test_raw_removal_uses_contrasting_danger_and_rightmost_confirmation(
    layout, name, affirmative, theme
):
    layout.apply(size=(1024, 600), theme=theme)
    dialog = layout.open_dialog(name)
    layout.bridge.setTheme(theme)
    pump(100)
    buttons = _buttons(dialog)
    accept = next(button for button in buttons if button["text"] == affirmative)
    cancel = next(button for button in buttons if button["text"] == "Cancel")
    assert QColor(accept["background"]).name() == ("#ff9a9a" if theme == "dark" else "#a8201a")
    assert _contrast(QColor(accept["background"]), QColor(accept["label"])) >= 4.5
    assert cancel["flat"] and not accept["flat"]
    assert accept["x"] > cancel["x"]
    assert accept["y"] == pytest.approx(cancel["y"])
    dialog.invoke("close")


def test_secondary_tray_actions_do_not_compete_with_accept(layout):
    layout.apply(size=(1920, 1080))
    assert evaluate(layout, "selectFlagged", "flat")
    assert evaluate(layout, "selectFailed", "flat")
    assert not evaluate(layout, "acceptSelection", "flat")


def test_first_load_done_has_primary_emphasis_and_cancel_is_secondary(layout):
    from dataclasses import replace

    layout.apply(size=(1920, 1080))
    layout.bridge._apply(replace(layout.main, load_phase="await_reinsert"))
    pump(100)
    assert not evaluate(layout, "firstLoadDoneButton", "flat")
    assert evaluate(
        layout,
        "firstLoadDoneButton",
        "children.some(child => child.objectName === 'pillFocusRing')",
    )
    assert evaluate(layout, "firstLoadCancelButton", "flat")


def test_start_body_scrolls_create_batch_clear_of_theme_footer(layout):
    from PySide6.QtCore import QObject
    from PySide6.QtQml import QQmlEngine, QQmlExpression

    from opticfilm_linux.app.controller import Controller
    from opticfilm_linux.app.prefs import MemoryPrefs
    from opticfilm_linux.app.qt.app import create_engine, scanner_factory
    from opticfilm_linux.app.qt.bridge import Bridge
    from opticfilm_linux.app.qt.images import PreviewProvider

    bridge = Bridge(Controller(scanner_factory(None)), MemoryPrefs())
    engine, warnings = create_engine(bridge, PreviewProvider(lambda *_: None), size=(1024, 600))
    root = engine.rootObjects()[0]
    try:
        pump(100)
        start = root.findChild(QObject, "startPage")
        expression = QQmlExpression(
            QQmlEngine.contextForObject(start),
            start,
            "verticalScrollBarPolicy === QQC2.ScrollBar.AlwaysOn",
        )
        assert expression.evaluate()[0], "the overflowing form needs a persistent scrollbar"
        expression.setExpression("flickable.contentY = flickable.contentHeight - flickable.height")
        expression.evaluate()
        assert not expression.hasError(), expression.error().toString()
        pump(50)
        nodes = tree(engine, root)
        action = named(nodes, "createBatch")
        theme = named(nodes, "themeToggle")
        assert 0 <= action.rect.y < action.rect.y + action.rect.height <= theme.rect.y
        assert warnings == []
    finally:
        bridge.shutdown()
        root.hide()
        engine.deleteLater()
