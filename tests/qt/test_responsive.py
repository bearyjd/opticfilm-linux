"""Small-screen layout, adaptive pane, and keyboard contracts."""

import json
import os

import pytest

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
pytest.importorskip("PySide6")

from dataclasses import replace

from layout_fixture import LayoutDialog, create_layout, pump, worst_stack
from layout_probe import (
    Rect,
    assert_reachable,
    detect,
    named,
    popup_rect,
    thumb_caption_violations,
    tree,
)
from PySide6.QtCore import QObject, QPoint, Qt
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from opticfilm_linux.scanner.base import ScannerStatus

SIZES = ((800, 600), (1024, 600), (1280, 800), (1920, 1080), (720, 540))
CASES = tuple(pytest.param(size, "dark", id=f"{size[0]}x{size[1]}-dark") for size in SIZES) + (
    pytest.param((800, 600), "light", id="800x600-light"),
)


@pytest.fixture(scope="module")
def layout(tmp_path_factory, sweep600_dir):
    app = QApplication.instance() or QApplication(["responsive-layout"])
    with create_layout(app, sweep600_dir, tmp_path_factory.mktemp("layout")) as fixture:
        yield fixture


def assert_clear(issues):
    assert not issues, "\n".join(f"{i.kind}: {i.item} {i.text!r} {i.rect}" for i in issues)


@pytest.mark.parametrize("size,theme", CASES)
def test_contact_sheet_controls_are_reachable(layout, size, theme):
    layout.apply(size=size, theme=theme)
    nodes = tree(layout.engine, layout.root)
    assert_pane_controls_reachable(
        layout,
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
def test_worst_stack_keeps_review_space_and_safety_actions(layout, size, theme):
    layout.apply("worst-stack", size, theme)
    collapse_status(layout)
    nodes = tree(layout.engine, layout.root)
    sheet = named(nodes, "sheetView")
    page = named(nodes, "mainPage")
    assert sheet.rect.height >= max(300, page.rect.height * 0.5), sheet.rect
    row = nodes[named(nodes, "sheetTray_1").parent]
    assert sheet.rect.height >= row.rect.height, "one complete tray row must fit"
    expand_status(layout)
    assert_pane_controls_reachable(
        layout,
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
    expand_status(layout)
    assert_reachable(tree(layout.engine, layout.root), layout.root, names)


def evaluate(layout, name, script):
    item = layout.root.findChild(QObject, name)
    assert item is not None, name
    expression = QQmlExpression(QQmlEngine.contextForObject(item), item, script)
    value, _undefined = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    pump(50)
    return value


def drawer(layout, side):
    return LayoutDialog(layout.engine, layout.root, side + "Drawer")


def open_drawer(layout, side):
    assert_reachable(tree(layout.engine, layout.root), layout.root, (side + "DrawerToggle",))
    evaluate(layout, side + "DrawerToggle", "clicked()")
    pump(350)
    popup = drawer(layout, side)
    assert popup.property("opened"), side
    return popup


def close_drawer(layout, popup):
    popup.invoke("close")
    pump(350)
    assert not popup.property("opened")


def assert_pane_controls_reachable(layout, names):
    groups = {
        "tray": {"selectFlagged", "selectFailed"},
        "inspector": {"acceptSelection", "reexportSelection", "undoSelection"},
    }
    nodes = tree(layout.engine, layout.root)
    present = {node.name for node in nodes}
    direct = tuple(name for name in names if name in present)
    assert_reachable(nodes, layout.root, direct)
    missing = set(names) - set(direct)
    if "themeToggle" in missing:
        assert evaluate(layout, "themeAction", "visible && enabled")
        missing.remove("themeToggle")
    for side, controls in groups.items():
        wanted = missing & controls
        if not wanted:
            continue
        popup = open_drawer(layout, side)
        try:
            nodes = tree(layout.engine, layout.root, popup)
            assert_reachable(nodes, layout.root, wanted)
            assert_clear(detect(layout.engine, layout.root, popup, nodes))
            missing -= wanted
        finally:
            close_drawer(layout, popup)
    assert not missing, missing


def expand_status(layout):
    nodes = tree(layout.engine, layout.root)
    if any(node.name == "statusMore" for node in nodes):
        evaluate(layout, "statusMore", "clicked()")


def collapse_status(layout):
    evaluate(layout, "statusScroll", "expanded = false; contentItem.contentY = 0")


def assert_visible_entries(nodes, keys):
    names = {
        "activeFirstLoad": "firstLoadCard",
        "error": "errorBanner",
        "held": "freeHeldRaw",
        "empty": "confirmEmptyFrames",
        "latch": "confirmHolderLatched",
        "firstLoad": "firstLoadCard",
    }
    expected = {names[key] for key in keys}
    actual = {node.name for node in nodes if node.name in names.values()}
    assert actual == expected


@pytest.mark.parametrize("size", ((800, 600), (1024, 600), (1920, 1080)))
@pytest.mark.parametrize(
    "phase", ("idle", "failed", "done", "jogging", "await_reinsert", "loading")
)
def test_status_priority_and_more_counts(layout, size, phase):
    layout.apply("worst-stack", size)
    layout.bridge._apply(
        replace(
            worst_stack(layout.main),
            load_phase=phase,
            empty_confirmation_tray=1,
            empty_confirmation_count=4,
        )
    )
    collapse_status(layout)
    active = phase in {"jogging", "await_reinsert", "loading"}
    keys = (["activeFirstLoad"] if active else []) + ["error", "held", "empty", "latch"]
    keys += [] if active else ["firstLoad"]
    assert json.loads(evaluate(layout, "statusScroll", "JSON.stringify(entryKeys)")) == keys
    limit = evaluate(layout, "statusScroll", "entryLimit")
    assert limit == (1 if size[1] == 600 else 3)
    nodes = tree(layout.engine, layout.root)
    assert_visible_entries(nodes, keys[:limit])
    expected_hidden = len(keys) - limit
    more = named(nodes, "statusMore")
    titles = {
        "activeFirstLoad": "First load",
        "error": "Error",
        "held": "Raw cleanup held",
        "empty": "Empty frames",
        "latch": "Holder latch",
        "firstLoad": "First load",
    }
    assert more.text == f"{expected_hidden} more: " + ", ".join(titles[key] for key in keys[limit:])
    assert evaluate(layout, "statusScroll", "visibleEntryCount") == limit
    assert evaluate(layout, "statusScroll", "hiddenEntryCount") == expected_hidden
    expand_status(layout)
    nodes = tree(layout.engine, layout.root)
    order = ("errorBanner", "freeHeldRaw", "confirmEmptyFrames", "confirmHolderLatched")
    order = ("firstLoadCard", *order) if active else (*order, "firstLoadCard")
    tops = [named(nodes, name).rect.y for name in order]
    assert tops == sorted(tops)
    assert evaluate(layout, "statusScroll", "visibleEntryCount") == len(keys)


@pytest.mark.parametrize("size", ((800, 600), (1024, 600)))
@pytest.mark.parametrize("scanner", (ScannerStatus.NOT_READY, ScannerStatus.INTERRUPTED))
def test_short_worst_stack_all_safety_actions_remain_reachable(layout, size, scanner):
    layout.apply("worst-stack", size)
    warning_start = len(layout.warnings)
    layout.bridge._apply(
        replace(
            worst_stack(layout.main),
            scanner=scanner,
            can_confirm_scanner=True,
            load_phase="await_reinsert",
            empty_confirmation_tray=1,
            empty_confirmation_count=4,
        )
    )
    collapse_status(layout)
    nodes = tree(layout.engine, layout.root)
    scanner_action = (
        "loadHelpLink" if scanner == ScannerStatus.NOT_READY else "confirmScannerButton"
    )
    assert_reachable(
        nodes,
        layout.root,
        ("scannerStatusText", scanner_action, "ejectButton", "loadNextButton", "statusMore"),
    )
    assert named(nodes, "sheetView").rect.height >= 300
    expand_status(layout)
    assert_pane_controls_reachable(
        layout,
        (
            "freeHeldRaw",
            "confirmEmptyFrames",
            "confirmHolderLatched",
            "firstLoadDoneButton",
            "firstLoadCancelButton",
            "bannerDetails",
            "bannerDismiss",
            "themeToggle",
        ),
    )
    assert evaluate(layout, "shortcutHelp", "visible && enabled")
    assert named(tree(layout.engine, layout.root), "sheetView").rect.height >= 300
    assert layout.warnings[warning_start:] == []


def test_latch_empty_label_and_plain_auto_status(layout):
    layout.apply("worst-stack", (1920, 1080))
    layout.bridge._apply(replace(worst_stack(layout.main), holder_latch_text=""))
    expand_status(layout)
    nodes = tree(layout.engine, layout.root)
    assert not any(node.name == "holderLatchText" for node in nodes)
    assert_reachable(nodes, layout.root, ("confirmHolderLatched",))
    layout.bridge._apply(
        replace(
            layout.main,
            can_first_load=False,
            holder_latch_text="",
            holder_latch_warning=False,
            can_confirm_holder_latched=False,
            load_phase="idle",
            auto_status="Waiting for export",
            auto_export=True,
        )
    )
    collapse_status(layout)
    nodes = tree(layout.engine, layout.root)
    assert named(nodes, "autoPipelineStatus").text == "Waiting for export"
    assert evaluate(layout, "statusScroll", "entryKeys.join(',')") == "auto"
    assert not any(node.name == "statusMore" for node in nodes)


def test_expanded_status_and_error_details_stay_inside_height_cap(layout):
    layout.apply("worst-stack", (800, 600))
    collapse_status(layout)
    expand_status(layout)
    evaluate(layout, "bannerDetails", "clicked()")
    nodes = tree(layout.engine, layout.root)
    assert named(nodes, "sheetView").rect.height >= 300
    scroll = named(nodes, "statusScroll").rect
    evaluate(
        layout,
        "statusScroll",
        "contentItem.contentY = contentItem.contentHeight - contentItem.height",
    )
    nodes = tree(layout.engine, layout.root)
    assert scroll.contains(named(nodes, "firstLoadButton").rect)
    evaluate(layout, "bannerDetails", "clicked()")
    collapse_status(layout)


def test_short_theme_and_shortcuts_keep_keyboard_navigation(layout, monkeypatch):
    layout.apply(size=(800, 600))
    collapse_status(layout)
    nodes = tree(layout.engine, layout.root)
    for name in ("themeAction", "shortcutHelp"):
        assert evaluate(layout, name, "visible && enabled")
    assert not any(node.text.startswith("Ctrl+A select all") for node in nodes)
    evaluate(layout, "shortcutHelp", "trigger()")
    pump(100)
    nodes = tree(layout.engine, layout.root)
    assert any(node.text.startswith("Ctrl+A select all") for node in nodes)
    QTest.keyClick(layout.root, Qt.Key.Key_Escape)
    pump(100)
    calls = []
    monkeypatch.setattr(layout.bridge, "moveSelectionFocus", lambda step, shift: calls.append(step))
    for key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
        QTest.keyClick(layout.root, key)
    assert calls == [-1, 1, -4, 4]
    evaluate(layout, "themeAction", "trigger()")
    assert layout.bridge.theme == "light"


def test_height_class_boundary_settles_without_qml_warnings(layout):
    layout.apply(size=(1366, 1000))
    warning_start = len(layout.warnings)
    for height in (1000, 950, 900, 850, 825, 810, 800, 780, 800, 850, 900, 950):
        layout.root.resize(1366, height)
        pump(100)
        expected = evaluate(layout, "mainPage", "height") < 45 * layout.root.property("gridUnit")
        assert evaluate(layout, "mainPage", "shortWindow") == expected, height
    assert layout.warnings[warning_start:] == []


@pytest.mark.parametrize("scanner", (ScannerStatus.NOT_READY, ScannerStatus.INTERRUPTED))
def test_scanner_action_order_survives_compact_reflow(layout, scanner):
    layout.apply(size=(800, 600))
    warning_start = len(layout.warnings)
    layout.bridge._apply(replace(layout.main, scanner=scanner, can_confirm_scanner=True))
    first = "loadHelpLink" if scanner == ScannerStatus.NOT_READY else "confirmScannerButton"
    names = (first, "ejectButton", "loadNextButton")
    for width in (800, 1024, 800, 1366, 800):
        layout.root.resize(width, 600)
        pump(100)
        nodes = tree(layout.engine, layout.root)
        positions = [(named(nodes, name).rect.y, named(nodes, name).rect.x) for name in names]
        assert positions == sorted(positions), (width, names, positions)
        assert_reachable(nodes, layout.root, names)
    assert layout.warnings[warning_start:] == []


def test_panes_keep_single_instances_and_state_when_reparented(layout):
    layout.apply(size=(1920, 1080))
    names = ("trayPane", "inspectorPanel", "trayQueue")
    originals = {name: layout.root.findChild(QQuickItem, name) for name in names}
    assert all(originals.values())
    evaluate(layout, "inspectorPanel", "showBefore = true")
    parents = {}
    for width in (1920, 1024, 800, 1920):
        layout.root.resize(width, 800)
        pump(150)
        for name, original in originals.items():
            assert layout.root.findChildren(QQuickItem, name) == [original], (width, name)
        assert originals["inspectorPanel"].property("showBefore") is True
        parents[width] = tuple(originals[name].parentItem() for name in names[:2])
        nodes = tree(layout.engine, layout.root)
        for side, visible in (("tray", width == 800), ("inspector", width < 1920)):
            toggle = layout.root.findChild(QQuickItem, side + "DrawerToggle")
            assert toggle.isVisible() is visible
            if visible:
                assert_reachable(nodes, layout.root, (side + "DrawerToggle",))
    assert parents[1920][0] is not parents[800][0]
    assert parents[1920][1] is not parents[1024][1]
    assert parents[1024][0] is parents[1920][0]
    evaluate(layout, "inspectorPanel", "showBefore = false")


@pytest.mark.parametrize("side", ("tray", "inspector"))
def test_drawer_escape_restores_the_complete_review_key_map(layout, monkeypatch, side):
    layout.apply(size=(800, 600))
    popup = open_drawer(layout, side)
    QTest.keyClick(layout.root, Qt.Key.Key_Escape)
    pump(350)
    assert not popup.property("opened")
    calls = []
    methods = (
        "moveSelectionFocus",
        "toggleSelectionAccepted",
        "setSelectionStatus",
        "rotateSelection",
        "mirrorSelection",
        "undo",
        "selectAll",
    )
    for method in methods:
        monkeypatch.setattr(
            layout.bridge,
            method,
            lambda *args, name=method: calls.append((name, args)),
        )
    keys = (
        Qt.Key.Key_Left,
        Qt.Key.Key_Right,
        Qt.Key.Key_Up,
        Qt.Key.Key_Down,
        Qt.Key.Key_Space,
        Qt.Key.Key_A,
        Qt.Key.Key_R,
        Qt.Key.Key_N,
        Qt.Key.Key_BracketLeft,
        Qt.Key.Key_BracketRight,
        Qt.Key.Key_M,
    )
    for key in keys:
        QTest.keyClick(layout.root, key)
    QTest.keyClick(layout.root, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(layout.root, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert calls == expected_review_calls()


def expected_review_calls():
    return [
        ("moveSelectionFocus", (-1, False)),
        ("moveSelectionFocus", (1, False)),
        ("moveSelectionFocus", (-4, False)),
        ("moveSelectionFocus", (4, False)),
        ("toggleSelectionAccepted", ()),
        ("setSelectionStatus", ("accepted",)),
        ("setSelectionStatus", ("rejected",)),
        ("setSelectionStatus", ("needs_look",)),
        ("rotateSelection", (-1,)),
        ("rotateSelection", (1,)),
        ("mirrorSelection", ()),
        ("undo", ()),
        ("selectAll", ()),
    ]


@pytest.mark.parametrize(
    "side,control,dialog_name",
    (
        ("tray", "recoverRaw1", "recoverDialog"),
        ("inspector", "reexportSelection", "reexportSelectionDialog"),
    ),
)
def test_drawer_confirmations_belong_to_page_and_survive_drawer_close(
    layout, side, control, dialog_name
):
    layout.apply(size=(800, 600))
    trays = tuple(replace(t, can_recover_raw=t.number == 1) for t in layout.main.trays)
    layout.bridge._apply(replace(layout.main, trays=trays))
    assert evaluate(
        layout,
        "mainPage",
        """
        [data, contentData, contentItem.data].some(items =>
            Array.from(items).some(item => item.objectName === NAME))
    """.replace("NAME", json.dumps(dialog_name)),
    )
    popup = open_drawer(layout, side)
    assert_reachable(tree(layout.engine, layout.root, popup), layout.root, (control,))
    click_popup_control(popup, control)
    confirmation = LayoutDialog(layout.engine, layout.root, dialog_name)
    pump(350)
    assert confirmation.property("opened")
    close_drawer(layout, popup)
    assert confirmation.property("opened"), "drawer close must keep page confirmation alive"
    if side == "tray":
        assert confirmation.property("tray") == 1
    nodes = tree(layout.engine, layout.root, confirmation)
    assert_clear(detect(layout.engine, layout.root, confirmation, nodes))
    controls = tuple(node for node in nodes if node.control and node.prop("inFooter"))
    assert controls
    viewport = Rect(0, 0, layout.root.width(), layout.root.height())
    assert all(viewport.contains(node.rect) for node in controls)
    confirmation.invoke("close")
    pump(350)


def click_popup_control(popup, control):
    assert popup.evaluate(
        """
        (function() {
            var pending = [contentItem];
            while (pending.length) {
                var item = pending.pop();
                if (item.objectName === NAME) { item.clicked(); return true; }
                for (var i = 0; i < item.children.length; ++i)
                    pending.push(item.children[i]);
            }
            return false;
        })()
    """.replace("NAME", json.dumps(control))
    )


def action_button_rect(layout, condition):
    script = """
        JSON.stringify((function() {
            var top = contentItem;
            while (top.parent) top = top.parent;
            var pending = [top];
            while (pending.length) {
                var item = pending.pop();
                if (!item.visible || item.opacity < 0.01) continue;
                if (item.action && (CONDITION)) {
                    var point = item.mapToItem(null, 0, 0);
                    return [point.x, point.y, item.width, item.height];
                }
                for (var i = 0; i < item.children.length; ++i)
                    pending.push(item.children[i]);
            }
            return null;
        })())
    """.replace("CONDITION", condition)
    result = json.loads(evaluate(layout, "mainPage", script))
    return Rect(*result) if result else None


def click_rect(layout, rect):
    assert rect and Rect(0, 0, layout.root.width(), layout.root.height()).contains(rect)
    point = QPoint(round(rect.x + rect.width / 2), round(rect.y + rect.height / 2))
    QTest.mouseClick(layout.root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    pump(350)


@pytest.mark.parametrize("name", ("loadHelpButton", "themeAction", "shortcutHelp"))
def test_toolbar_overflow_menu_reaches_secondary_actions(layout, name):
    layout.apply(size=(800, 600))
    more = action_button_rect(layout, 'item.action.tooltip === "More Actions"')
    click_rect(layout, more)
    menu_condition = 'String(item).indexOf("MenuItem") >= 0'
    assert action_button_rect(layout, menu_condition), "overflow menu must reveal actions"
    target = "item.action.objectName === " + json.dumps(name)
    button = action_button_rect(layout, target + " && " + menu_condition)
    if button is None:
        QTest.keyClick(layout.root, Qt.Key.Key_Escape)
        pump(350)
        button = action_button_rect(layout, target)
    click_rect(layout, button)
    if name == "loadHelpButton":
        assert layout.dialog("load-help").property("opened")
        layout.dialog("load-help").invoke("close")
    elif name == "themeAction":
        assert layout.bridge.theme == "light"
    else:
        assert any(
            node.text.startswith("Ctrl+A select all") for node in tree(layout.engine, layout.root)
        )
        QTest.keyClick(layout.root, Qt.Key.Key_Escape)
    pump(350)
