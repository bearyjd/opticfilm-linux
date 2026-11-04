"""Status area contract: height floor, pinned 'N more', review minimum, preserved Details."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
pytest.importorskip("PySide6")

from dataclasses import replace

from layout_fixture import create_layout, worst_stack
from layout_probe import Rect, named, tree
from PySide6.QtWidgets import QApplication
from test_responsive import collapse_status, evaluate, expand_status

WORKER = Path(__file__).with_name("status_area_worker.py")
ROOT = Path(__file__).resolve().parents[2]
SCANNER_ROW = ("scannerStatusText", "ejectButton", "loadNextButton")


@pytest.fixture(scope="module")
def layout(tmp_path_factory, sweep600_dir):
    app = QApplication.instance() or QApplication(["status-area"])
    with create_layout(app, sweep600_dir, tmp_path_factory.mktemp("status")) as fixture:
        yield fixture


def control_height(gu):
    return max(44, round(gu * 2.45))


def contains(outer, inner):
    return Rect.contains(outer, inner)


def test_tiny_page_keeps_scanner_row(layout):
    layout.apply("worst-stack", (320, 240))
    collapse_status(layout)
    gu = layout.root.property("gridUnit")
    nodes = tree(layout.engine, layout.root)
    scroll = named(nodes, "statusScroll").rect
    assert scroll.height >= 2 * control_height(gu)
    assert scroll.y + scroll.height <= evaluate(layout, "mainPage", "height")
    for name in SCANNER_ROW:
        assert contains(scroll, named(nodes, name).rect), name
    layout.apply(size=(1220, 780))


def test_large_font_keeps_scanner_row(sweep600_dir, tmp_path):
    output = tmp_path / "result.json"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software")
    env["PYTHONPATH"] = os.pathsep.join(str(ROOT / part) for part in ("src", "tests", "tests/qt"))
    for kind in ("STATE", "CACHE", "CONFIG", "DATA"):
        env[f"XDG_{kind}_HOME"] = str(tmp_path / kind.lower())
    command = [sys.executable, str(WORKER), str(sweep600_dir), str(tmp_path), str(output), "2"]
    done = subprocess.run(command, env=env, capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-4000:]
    result = json.loads(output.read_text())
    assert result["warnings"] == []
    for case in result["cases"]:
        assert case["gu"] >= 30, case
        scroll = Rect(*case["scroll"])
        assert scroll.height >= 2 * control_height(case["gu"]), case
        assert scroll.y + scroll.height <= case["size"][1], case
        for name, rect in case["row"].items():
            assert contains(scroll, Rect(*rect)), (case["size"], name)


def test_more_button_and_short_row_stay_pinned_when_content_exceeds_cap(layout):
    layout.apply("worst-stack", (800, 600))
    collapse_status(layout)
    expand_status(layout)
    for script in ("contentItem.contentY = 0", "contentItem.contentY = contentItem.contentHeight"):
        evaluate(layout, "statusScroll", script)
        nodes = tree(layout.engine, layout.root)
        scroll = named(nodes, "statusScroll").rect
        assert evaluate(layout, "statusScroll", "contentItem.contentHeight > contentItem.height")
        assert named(nodes, "statusMore").text == "Show less"
        for name in ("statusMore", "shortcutHelp", "themeToggle"):
            assert contains(scroll, named(nodes, name).rect), (script, name)
    collapse_status(layout)


def test_review_area_keeps_its_minimum(layout):
    for size in ((1280, 1000), (1280, 820), (800, 600)):
        layout.apply("worst-stack", size)
        expand_status(layout)
        gu = layout.root.property("gridUnit")
        page_height = evaluate(layout, "mainPage", "height")
        column_height = evaluate(layout, "statusScroll", "parent.height")
        status = named(tree(layout.engine, layout.root), "statusScroll").rect
        review = named(tree(layout.engine, layout.root), "reviewArea").rect
        minimum = max(17 * gu, 0.5 * page_height)
        assert review.height >= minimum - 1, size
        # the status area is capped against the same column the review minimum is enforced in
        floor = 2 * control_height(gu)
        assert status.height <= max(floor, column_height - minimum) + 1, size
        collapse_status(layout)


def test_expanded_details_survive_hide_and_show(layout):
    layout.apply("worst-stack", (800, 600))
    collapse_status(layout)
    evaluate(layout, "bannerDetails", "clicked()")
    assert evaluate(layout, "errorBanner", "showDetail")
    # the first-load card activating pushes the error below the short-window cap
    layout.bridge._apply(replace(worst_stack(layout.main), load_phase="jogging"))
    assert not any(n.name == "errorBanner" for n in tree(layout.engine, layout.root))
    layout.bridge._apply(worst_stack(layout.main))
    assert evaluate(layout, "errorBanner", "showDetail")
    layout.bridge._apply(replace(worst_stack(layout.main), banner=None))
    assert not evaluate(layout, "errorBanner", "showDetail")


def test_first_load_card_is_denser_when_compact(layout):
    layout.apply("worst-stack", (1920, 1080))
    regular = evaluate(layout, "firstLoadCard", "padding")
    layout.apply("worst-stack", (800, 600))
    assert evaluate(layout, "firstLoadCard", "compact")
    assert evaluate(layout, "firstLoadCard", "padding") < regular
    layout.apply("worst-stack", (1920, 1080))
    assert evaluate(layout, "firstLoadCard", "padding") == regular


def test_expanded_resets_when_nothing_is_hidden(layout):
    layout.apply("worst-stack", (800, 600))
    expand_status(layout)
    assert evaluate(layout, "statusScroll", "expanded")
    layout.bridge._apply(replace(layout.main, can_first_load=False, load_phase="idle"))
    assert evaluate(layout, "statusScroll", "hiddenEntryCount") == 0
    assert not evaluate(layout, "statusScroll", "expanded")
    layout.bridge._apply(worst_stack(layout.main))
    assert evaluate(layout, "statusScroll", "hiddenEntryCount") > 0
    assert named(tree(layout.engine, layout.root), "statusMore").text.endswith(
        "Raw cleanup held, Holder latch, First load"
    )


def test_empty_auto_status_takes_no_line(layout):
    layout.apply(size=(1920, 1080))
    layout.bridge._apply(
        replace(
            layout.main,
            can_first_load=False,
            load_phase="idle",
            holder_latch_text="",
            holder_latch_warning=False,
            can_confirm_holder_latched=False,
            auto_export=True,
            auto_status="",
        )
    )
    assert evaluate(layout, "statusScroll", "entryKeys.join(',')") == "auto"
    assert not any(n.name == "autoPipelineStatus" for n in tree(layout.engine, layout.root))


def test_theme_host_follows_the_toggle_width(layout):
    layout.apply(size=(800, 600))
    host = evaluate(layout, "mainPage", "compactThemeHost.implicitWidth")
    assert host == evaluate(layout, "themeToggle", "implicitWidth") > 0
