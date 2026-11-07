"""Fresh-process probe: status area under a large application font (gu ~ 36)."""

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from dataclasses import replace

from layout_fixture import create_layout, pump, worst_stack
from layout_probe import named, tree
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

SCANNER_ROW = ("scannerStatusText", "ejectButton", "loadNextButton")


def probe(fixture, size):
    fixture.apply("worst-stack", size)
    fixture.bridge._apply(replace(worst_stack(fixture.main), can_eject=True, can_load_next=True))
    pump(100)
    nodes = tree(fixture.engine, fixture.root)
    scroll = named(nodes, "statusScroll").rect
    return {
        "size": [fixture.root.width(), fixture.root.height()],
        "gu": fixture.root.property("gridUnit"),
        "scroll": [scroll.x, scroll.y, scroll.width, scroll.height],
        "row": {name: _rect(named(nodes, name).rect) for name in SCANNER_ROW},
    }


def _rect(rect):
    return [rect.x, rect.y, rect.width, rect.height]


def main():
    sweep, directory, output, scale = sys.argv[1:5]
    app = QApplication(["status-area"])
    font = QFont(app.font())
    font.setPointSizeF(font.pointSizeF() * float(scale))
    app.setFont(font)  # Must precede construction of the QML engine and Kirigami Units.
    with create_layout(app, Path(sweep), Path(directory)) as fixture:
        cases = [probe(fixture, size) for size in ((800, 600), (480, 320))]
        Path(output).write_text(json.dumps({"cases": cases, "warnings": fixture.warnings}))


if __name__ == "__main__":
    main()
