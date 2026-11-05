"""The metadata strip fits every supported window: compact, short and wide."""

import os

import pytest

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
pytest.importorskip("PySide6")

from layout_fixture import create_layout
from layout_probe import assert_reachable, detect, named, tree
from PySide6.QtWidgets import QApplication
from test_responsive import SIZES, evaluate

CONTROLS = ("metaDescription", "metaYear", "metaMonth", "metaClear", "metaLevelDate")


@pytest.fixture(scope="module")
def layout(tmp_path_factory, sweep600_dir):
    app = QApplication.instance() or QApplication(["strip-layout"])
    with create_layout(app, sweep600_dir, tmp_path_factory.mktemp("strip")) as fixture:
        yield fixture


@pytest.mark.parametrize("size", SIZES, ids=[f"{w}x{h}" for w, h in SIZES])
def test_the_strip_and_its_controls_fit(layout, size):
    layout.apply(size=size)
    short = evaluate(layout, "mainPage", "shortWindow")
    if short:  # one row by default; the date and the actions open on request
        assert not evaluate(layout, "metaYear", "visible")
        evaluate(layout, "metaDateToggle", "clicked()")
    try:
        nodes = tree(layout.engine, layout.root)
        assert named(nodes, "metadataStrip").rect.height > 0
        assert_reachable(nodes, layout.root, CONTROLS)
        issues = [
            i
            for i in detect(layout.engine, layout.root, nodes=nodes)
            if i.item.startswith("meta")
            and i.kind in {"control_outside_window", "outside_clipping_parent"}
        ]
        assert not issues, issues
    finally:
        if short:
            evaluate(layout, "metaDateToggle", "clicked()")
