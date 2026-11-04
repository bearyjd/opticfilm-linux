"""Rendered status viewport, pinned scanner controls and toolbar width regressions."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
pytest.importorskip("PySide6")

from layout_fixture import create_layout, worst_stack
from layout_probe import Rect, named, tree
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from test_responsive import collapse_status, evaluate, expand_status

ROOT = Path(__file__).resolve().parents[2]


def viewport(layout):
    encoded = evaluate(
        layout,
        "statusScroll",
        """JSON.stringify((function() {
        const p = contentItem.mapToItem(null, 0, 0);
        return [p.x, p.y, contentItem.width, contentItem.height];
    })())""",
    )
    return Rect(*json.loads(encoded))


def scroll_control_into_view(layout, name):
    evaluate(layout, "statusScroll", "contentItem.contentY = 0")
    view = viewport(layout)
    control = named(tree(layout.engine, layout.root), name).rect
    offset = max(0, control.y + control.height - view.y - view.height)
    evaluate(layout, "statusScroll", f"contentItem.contentY = {offset}")
    assert viewport(layout).contains(named(tree(layout.engine, layout.root), name).rect), name


def check_viewport(layout, size):
    layout.apply("worst-stack", size)
    layout.bridge._apply(replace(worst_stack(layout.main), load_phase="await_reinsert"))
    collapse_status(layout)
    nodes = tree(layout.engine, layout.root)
    view = viewport(layout)
    content_height = evaluate(layout, "statusScroll", "contentHeight")
    if content_height <= evaluate(layout, "statusScroll", "budget - bottomPadding"):
        assert abs(view.height - content_height) < 1, "a complete entry should fit without clipping"
    scanner = named(nodes, "scannerStatusText").rect
    assert scanner.y >= view.y + view.height, (view, scanner)
    control_height = evaluate(layout, "mainPage", "toolbarFloor")
    assert view.height >= control_height * (1.5 if size[1] >= 600 else 1)
    scroll_control_into_view(layout, "firstLoadDoneButton")
    if size == (800, 600):
        assert named(nodes, "sheetView").rect.height >= 240
        assert view.contains(named(nodes, "firstLoadCard").rect), (
            "the active entry must fit in full"
        )
    for name in ("trayDrawerToggle", "inspectorDrawerToggle"):
        assert not evaluate(layout, name, "visible") or evaluate(
            layout, name, "width >= implicitWidth"
        )
    assert named(nodes, "primaryActionButton").rect.x > named(nodes, "mainPage").rect.x
    return {"size": size, "viewport": list(view.__dict__.values()), "scanner": scanner.y}


def check_expanded(layout):
    layout.apply("worst-stack", (800, 600))
    collapse_status(layout)
    expand_status(layout)
    nodes = tree(layout.engine, layout.root)
    before = named(nodes, "ejectButton").rect
    assert evaluate(layout, "statusScroll", "overflowing")
    assert evaluate(layout, "statusVerticalScrollBar", "visible && policy === 2")
    bar = named(nodes, "statusVerticalScrollBar").rect
    scroll = named(nodes, "statusScroll").rect
    assert bar.height == viewport(layout).height
    assert bar.height > bar.width
    assert abs(bar.x + bar.width - scroll.x - scroll.width) < 1
    assert any(node.name == "statusMoreBelow" for node in nodes)
    assert not any(node.name == "holderLatchText" for node in nodes)
    for name in (
        "bannerDetails",
        "bannerDismiss",
        "freeHeldRaw",
        "confirmHolderLatched",
        "firstLoadButton",
    ):
        scroll_control_into_view(layout, name)
        assert named(tree(layout.engine, layout.root), "ejectButton").rect == before
    evaluate(layout, "statusScroll", "contentItem.contentY = contentHeight - availableHeight")
    assert not any(node.name == "statusMoreBelow" for node in tree(layout.engine, layout.root))


@pytest.mark.parametrize("scale", (1, 1.5))
def test_real_status_viewport_at_font_scales(sweep600_dir, tmp_path, scale):
    output = tmp_path / "status-polish.json"
    env = dict(
        os.environ, PYTHONPATH=os.pathsep.join(str(ROOT / p) for p in ("src", "tests", "tests/qt"))
    )
    for kind in ("STATE", "CACHE", "CONFIG", "DATA"):
        env[f"XDG_{kind}_HOME"] = str(tmp_path / kind.lower())
    command = [sys.executable, __file__, str(sweep600_dir), str(tmp_path), str(output), str(scale)]
    done = subprocess.run(command, env=env, capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-5000:]
    result = json.loads(output.read_text())
    assert result["warnings"] == []


def main():
    sweep, directory, output, scale = sys.argv[1:]
    app = QApplication(["status-polish"])
    font = QFont(app.font())
    font.setPointSizeF(font.pointSizeF() * float(scale))
    app.setFont(font)
    with create_layout(app, Path(sweep), Path(directory)) as layout:
        cases = [check_viewport(layout, size) for size in ((800, 600), (1024, 600), (720, 540))]
        check_expanded(layout)
        Path(output).write_text(json.dumps({"cases": cases, "warnings": layout.warnings}))


if __name__ == "__main__":
    main()
