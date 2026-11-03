"""Theme and keyboard regressions for the remaining workflow surfaces."""

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
pytest.importorskip("PySide6")

from PySide6.QtCore import QMetaObject, QObject, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from opticfilm_linux.app.controller import Controller
from opticfilm_linux.app.prefs import MemoryPrefs
from opticfilm_linux.app.qt.app import configure_style, scanner_factory
from opticfilm_linux.app.qt.bridge import Bridge


@pytest.fixture
def dialogs():
    app = QApplication.instance() or QApplication(["dialogs"])
    configure_style()
    bridge = Bridge(Controller(scanner_factory(None)), MemoryPrefs())
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda entries: warnings.extend(entry.toString() for entry in entries))
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("DialogHarness.qml"))))
    assert engine.rootObjects(), warnings
    root = engine.rootObjects()[0]
    QTest.qWait(100)
    yield app, bridge, root, warnings
    root.close()
    bridge.shutdown()
    engine.deleteLater()


def _wait_closed(dialog):
    for _ in range(100):
        if not dialog.property("visible"):
            return
        QTest.qWait(10)
    raise AssertionError("dialog did not close")


@pytest.mark.parametrize("name", ["Export", "FreeSpace", "Orientation", "LoadHelp", "Confirm"])
def test_dialog_surfaces_and_buttons_follow_live_theme(dialogs, name):
    _, bridge, root, warnings = dialogs
    dialog = root.findChild(QObject, "sample" + name)
    QMetaObject.invokeMethod(dialog, "open")
    QTest.qWait(100)
    for theme, surface, accent in [
        ("dark", "#232a38", "#83b4ff"),
        ("light", "#ffffff", "#255caa"),
    ]:
        bridge.setTheme(theme)
        QTest.qWait(100)
        background = dialog.property("background")
        assert background.property("color").name() == surface
        assert background.property("radius") == 14
        content = dialog.property("contentItem")
        colors = root.labelColors(content).toVariant()
        assert colors
        expected_text = {"#f1f5fc", "#abb8ce"} if theme == "dark" else {"#172133", "#53637b"}
        assert set(colors) <= expected_text
        footer = dialog.property("footer")
        buttons = root.pillButtons(content).toVariant() + root.pillButtons(footer).toVariant()
        assert buttons
        for button in buttons:
            assert button.property("implicitHeight") == 44
            if button.isEnabled():
                assert button.property("background").property("color").name() == accent
    QMetaObject.invokeMethod(dialog, "close")
    assert warnings == []


def test_confirmation_keeps_keyboard_accept_reject_and_focus(dialogs):
    _, _, root, warnings = dialogs
    dialog = root.findChild(QObject, "sampleConfirm")
    QMetaObject.invokeMethod(dialog, "open")
    QTest.qWait(100)
    buttons = root.pillButtons(dialog.property("footer")).toVariant()
    assert len(buttons) == 2
    yes = next(button for button in buttons if button.property("text").replace("&", "") == "Yes")
    yes.forceActiveFocus(Qt.TabFocusReason)
    QTest.qWait(20)
    assert yes.property("visualFocus")
    QTest.keyClick(root, Qt.Key_Tab)
    QTest.qWait(20)
    assert root.activeFocusItem() is not yes
    QTest.keyClick(root, Qt.Key_Backtab)
    QTest.qWait(20)
    assert root.activeFocusItem() is yes
    QTest.keyClick(root, Qt.Key_Space)
    QTest.qWait(100)
    assert root.property("confirmations") == 1
    _wait_closed(dialog)
    QMetaObject.invokeMethod(dialog, "open")
    QTest.qWait(100)
    QTest.keyClick(root, Qt.Key_Escape)
    QTest.qWait(100)
    _wait_closed(dialog)
    assert root.property("confirmations") == 1
    assert root.property("rejections") == 1
    assert warnings == []


def test_start_page_uses_rounded_card_and_pill_actions(dialogs):
    _, bridge, root, warnings = dialogs
    card = root.findChild(QQuickItem, "newBatchCard")
    create = root.findChild(QQuickItem, "createBatch")
    assert card is not None
    assert create.findChild(QQuickItem, "pillFocusRing") is not None
    assert not create.isEnabled()
    for theme, surface in [("dark", "#232a38"), ("light", "#ffffff")]:
        bridge.setTheme(theme)
        QTest.qWait(100)
        assert card.property("background").property("color").name() == surface
        assert card.property("background").property("radius") == 14
    assert warnings == []


@pytest.mark.parametrize("name", ["Export", "FreeSpace", "LoadHelp"])
def test_idle_dialogs_close_from_keyboard(dialogs, name):
    _, _, root, warnings = dialogs
    dialog = root.findChild(QObject, "sample" + name)
    QMetaObject.invokeMethod(dialog, "open")
    QTest.qWait(100)
    if name == "LoadHelp":
        buttons = root.pillButtons(dialog.property("footer")).toVariant()
        assert len(buttons) == 1
        buttons[0].forceActiveFocus(Qt.TabFocusReason)
        QTest.keyClick(root, Qt.Key_Space)
    else:
        QTest.keyClick(root, Qt.Key_Escape)
    QTest.qWait(100)
    _wait_closed(dialog)
    assert warnings == []


def test_error_banner_keeps_details_and_dismiss_actions(dialogs, tmp_path):
    _, bridge, root, warnings = dialogs
    bridge.openBatch(str(tmp_path / "missing"), False)
    _wait_banner(bridge, True)
    banner = root.findChild(QQuickItem, "errorBanner")
    details = root.findChild(QQuickItem, "bannerDetails")
    detail = root.findChild(QQuickItem, "bannerDetail")
    assert banner.isVisible() and not detail.isVisible()
    details.forceActiveFocus(Qt.TabFocusReason)
    QTest.keyClick(root, Qt.Key_Space)
    QTest.qWait(50)
    assert banner.property("showDetail") and detail.isVisible()
    for theme, surface in [("dark", "#232a38"), ("light", "#ffffff")]:
        bridge.setTheme(theme)
        QTest.qWait(50)
        assert banner.property("background").property("color").name() == surface
    dismiss = root.findChild(QQuickItem, "bannerDismiss")
    dismiss.forceActiveFocus(Qt.TabFocusReason)
    QTest.qWait(50)
    assert root.activeFocusItem() is dismiss
    clicked = QSignalSpy(dismiss.clicked)
    QTest.keyClick(root, Qt.Key_Space)
    assert clicked.count() == 1
    QTest.qWait(50)
    bridge.poll()
    QTest.qWait(50)
    _wait_banner(bridge, False)
    assert not banner.isVisible() and not banner.property("showDetail")
    assert warnings == []


def _wait_banner(bridge, visible):
    for _ in range(100):
        bridge.poll()
        if bridge.hasBanner == visible:
            return
        QTest.qWait(10)
    raise AssertionError("banner did not update")
