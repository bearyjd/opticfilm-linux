"""Fresh-process probe: toolbar overflow under a large application font (gu ~ 30)."""

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from layout_fixture import create_layout, pump
from layout_probe import named, tree
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_responsive import action_button_rect, click_rect, evaluate

MORE = 'item.action.tooltip === "More Actions"'
THEME = 'item.action.objectName === "themeAction" && String(item).indexOf("MenuItem") >= 0'


def _rect(rect):
    return [rect.x, rect.y, rect.width, rect.height] if rect else None


def probe(fixture, size):
    fixture.apply(size=size)
    more = action_button_rect(fixture, MORE)
    toolbar = named(tree(fixture.engine, fixture.root), "secondaryActionToolBar").rect
    if more and more.x >= 0 and more.x + more.width <= fixture.root.width():
        click_rect(fixture, more)
    theme = action_button_rect(fixture, THEME)
    QTest.keyClick(fixture.root, Qt.Key.Key_Escape)  # close the menu before the next size
    pump(350)
    return {
        "size": [fixture.root.width(), fixture.root.height()],
        "gu": fixture.root.property("gridUnit"),
        "toolbar": _rect(toolbar),
        "more": _rect(more),
        "theme": _rect(theme),
    }


def toggle_widths(fixture):
    """The Inspector toggle's width for an empty and a two-digit selection (must not jitter)."""
    widths = []
    for count in (0, 16):
        summary = replace(fixture.main.selection_summary, count=count)
        fixture.bridge._apply(replace(fixture.main, selection_summary=summary))
        pump(100)
        widths.append(evaluate(fixture, "inspectorDrawerToggle", "width"))
    return widths


def main():
    sweep, directory, output, scale = sys.argv[1:5]
    sizes = [tuple(size) for size in json.loads(sys.argv[5])] if len(sys.argv) > 5 else None
    app = QApplication(["toolbar"])
    font = app.font()
    font.setPointSizeF(font.pointSizeF() * float(scale))
    app.setFont(font)  # Must precede construction of the QML engine and Kirigami Units.
    with create_layout(app, Path(sweep), Path(directory)) as fixture:
        cases = [probe(fixture, size) for size in sizes or ((480, 320), (800, 600), (1024, 600))]
        widths = toggle_widths(fixture)
        result = {"cases": cases, "toggle_widths": widths, "warnings": fixture.warnings}
        Path(output).write_text(json.dumps(result))


if __name__ == "__main__":
    main()
