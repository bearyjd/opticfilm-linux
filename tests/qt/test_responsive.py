"""Small-screen layout contract; strict expected failures track later PRs."""

import os

import pytest

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
pytest.importorskip("PySide6")

from dataclasses import replace

from layout_fixture import create_layout, pump, worst_stack
from layout_probe import (
    Rect,
    assert_reachable,
    detect,
    named,
    popup_rect,
    thumb_caption_violations,
    tree,
)
from PySide6.QtWidgets import QApplication

from opticfilm_linux.scanner.base import ScannerStatus

SIZES = ((800, 600), (1024, 600), (1280, 800), (1920, 1080), (720, 540))
CASES = tuple(pytest.param(size, "dark", id=f"{size[0]}x{size[1]}-dark") for size in SIZES) + (
    pytest.param((800, 600), "light", id="800x600-light"),
)
PANES_PENDING = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="PR5: adaptive panes and toolbar overflow"
)
STATUS_PENDING = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="PR4: bounded prioritized status area"
)
DIALOGS_PENDING = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="PR3: bounded dialogs and visible footer actions"
)


@pytest.fixture(scope="module")
def layout(tmp_path_factory, sweep600_dir):
    app = QApplication.instance() or QApplication(["responsive-layout"])
    with create_layout(app, sweep600_dir, tmp_path_factory.mktemp("layout")) as fixture:
        yield fixture


def assert_clear(issues):
    assert not issues, "\n".join(f"{i.kind}: {i.item} {i.text!r} {i.rect}" for i in issues)


@pytest.mark.parametrize(
    "size,theme",
    tuple(
        pytest.param(
            size,
            theme,
            marks=PANES_PENDING if size[0] < 1280 else (),
            id=f"{size[0]}x{size[1]}-{theme}",
        )
        for size, theme in ((s, "dark") for s in SIZES)
    )
    + (pytest.param((800, 600), "light", marks=PANES_PENDING, id="800x600-light"),),
)
def test_contact_sheet_controls_are_reachable(layout, size, theme):
    layout.apply(size=size, theme=theme)
    nodes = tree(layout.engine, layout.root)
    assert_reachable(
        nodes,
        layout.root,
        (
            "primaryActionButton",
            "ejectButton",
            "loadNextButton",
            "acceptSelection",
            "reexportSelection",
            "undoSelection",
            "selectFlagged",
            "themeToggle",
        ),
    )
    assert_clear(
        tuple(
            i
            for i in detect(layout.engine, layout.root, nodes=nodes)
            if i.kind in {"control_outside_window", "outside_clipping_parent"}
        )
    )
    assert not thumb_caption_violations(nodes), "SlideThumb captions must fit at supported widths"


@pytest.mark.parametrize("size,theme", CASES)
@STATUS_PENDING
def test_worst_stack_keeps_review_space_and_safety_actions(layout, size, theme):
    layout.apply("worst-stack", size, theme)
    nodes = tree(layout.engine, layout.root)
    sheet = named(nodes, "sheetView")
    page = named(nodes, "mainPage")
    assert sheet.rect.height >= max(300, page.rect.height * 0.5), sheet.rect
    row = named(nodes, "sheetTray_1")
    assert sheet.rect.height >= row.rect.height, "one complete tray row must fit"
    assert_reachable(
        nodes,
        layout.root,
        (
            "freeHeldRaw",
            "confirmHolderLatched",
            "firstLoadButton",
            "bannerDetails",
            "bannerDismiss",
        ),
    )


@pytest.mark.parametrize(
    "name",
    tuple(
        pytest.param(
            name,
            marks=DIALOGS_PENDING if name not in {"confirm-cleanup", "first-load-quit"} else (),
        )
        for name in (
            "export",
            "free-space",
            "confirm-cleanup",
            "load-help",
            "empty-frames",
            "orientation",
            "first-load-quit",
        )
    ),
)
def test_dialog_bounds_and_actions_fit_without_scrolling(layout, name):
    layout.apply(size=(800, 600))
    warning_start = len(layout.warnings)
    dialog = layout.open_dialog(name)
    nodes = tree(layout.engine, layout.root, dialog)
    viewport = Rect(0, 0, layout.root.width(), layout.root.height())
    assert viewport.contains(popup_rect(dialog)), popup_rect(dialog)
    controls = tuple(node for node in nodes if node.control and node.prop("inFooter"))
    assert controls, f"{name} needs visible footer actions"
    for node in controls:
        assert viewport.contains(node.rect), f"{name}: {node.name or node.text} {node.rect}"
    assert_clear(
        tuple(
            i
            for i in detect(layout.engine, layout.root, dialog, nodes)
            if i.kind
            in {"control_outside_window", "outside_clipping_parent", "dialog_exceeds_window"}
        )
    )
    assert layout.warnings[warning_start:] == []


@pytest.mark.parametrize("size,theme", CASES)
def test_contact_sheet_engine_has_no_qml_warnings(layout, size, theme):
    assert layout.startup_warnings == ()
    warning_start = len(layout.warnings)
    layout.apply(size=size, theme=theme)
    assert layout.warnings[warning_start:] == []


@pytest.mark.parametrize(
    "state,names",
    (
        ("empty", ("confirmEmptyFrames",)),
        ("reset", ("confirmScannerButton",)),
        ("first-load", ("firstLoadDoneButton", "firstLoadCancelButton")),
        ("recovery", ("recoverRaw1",)),
    ),
)
def test_state_specific_safety_actions_are_reachable(layout, state, names):
    layout.apply(size=(1920, 1080))
    changes = {
        "empty": dict(empty_confirmation_tray=1, empty_confirmation_count=4),
        "reset": dict(scanner=ScannerStatus.INTERRUPTED, can_confirm_scanner=True),
        "first-load": dict(load_phase="await_reinsert"),
        "recovery": dict(
            trays=tuple(replace(t, can_recover_raw=t.number == 1) for t in layout.main.trays)
        ),
    }
    layout.bridge._apply(replace(worst_stack(layout.main), **changes[state]))
    pump(100)
    assert_reachable(tree(layout.engine, layout.root), layout.root, names)
