"""Render the shared components and native controls through the production palette bindings."""

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QPalette
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from opticfilm_linux.app.controller import Controller
from opticfilm_linux.app.prefs import MemoryPrefs
from opticfilm_linux.app.qt.app import configure_style, create_engine, scanner_factory
from opticfilm_linux.app.qt.bridge import Bridge
from opticfilm_linux.app.qt.images import PreviewProvider
from opticfilm_linux.app.theme import palette_colors


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(["theme"])
    configure_style()
    return app


def _click(root, item):
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, QPoint(round(point.x()), round(point.y())))
    QTest.qWait(50)


def _visual_child(item, name):
    if item.objectName() == name:
        return item
    for child in item.childItems():
        found = _visual_child(child, name)
        if found is not None:
            return found
    return None


def test_theme_toggle_persists_across_fresh_bridge(qapp):
    prefs = MemoryPrefs()
    first = Bridge(Controller(scanner_factory(None)), prefs)
    provider = PreviewProvider(lambda *_: None)
    engine, warnings = create_engine(first, provider)
    second = None
    try:
        assert engine.rootObjects(), warnings
        assert first.theme == "dark"
        root = engine.rootObjects()[0]
        toggle = root.findChild(QQuickItem, "themeToggle")
        assert toggle is not None
        QTest.qWait(100)
        dark = _visual_child(toggle, "segment0")
        light = _visual_child(toggle, "segment1")
        assert dark is not None and light is not None
        _click(root, dark)
        assert dark.property("checked") and not light.property("checked")
        _click(root, light)
        assert first.theme == prefs.get("theme") == "light"
        _click(root, light)
        assert light.property("checked") and not dark.property("checked")
        second = Bridge(Controller(scanner_factory(None)), prefs)
        assert second.theme == "light"
        first.setTheme("unknown")
        assert first.theme == "light"
        assert warnings == []
    finally:
        if second is not None:
            second.shutdown()
        first.shutdown()
        for root in engine.rootObjects():
            root.close()
        engine.deleteLater()


def _sample(image, item, x, y):
    point = item.mapToScene(QPointF(x, y))
    return image.pixelColor(round(point.x()), round(point.y())).name()


def _logical_image(root, path):
    image = root.grabWindow()
    assert not image.isNull()
    ratio = root.devicePixelRatio()
    assert (image.width(), image.height()) == (
        round(root.width() * ratio),
        round(root.height() * ratio),
    )
    assert image.save(str(path))  # Preserve the full physical-resolution evidence.
    return image.scaled(root.width(), root.height())


def _contrast(first, second):
    def luminance(color):
        channels = (color.redF(), color.greenF(), color.blueF())
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))

    low, high = sorted((luminance(first), luminance(second)))
    return (high + 0.05) / (low + 0.05)


def test_components_and_native_controls_repaint_in_both_themes(qapp, tmp_path):
    bridge = Bridge(Controller(scanner_factory(None)), MemoryPrefs())
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda entries: warnings.extend(entry.toString() for entry in entries))
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("ThemeHarness.qml"))))
    try:
        assert engine.rootObjects(), warnings
        assert QQuickStyle.name() == "Basic"
        root = engine.rootObjects()[0]
        items = {
            name: root.findChild(QQuickItem, "sample" + name)
            for name in ("Card", "Pill", "Segments", "Slider", "Check", "Heading")
        }
        assert all(item is not None for item in items.values())
        snapshots = {}
        for mode, surface, accent, text, track, window in [
            ("dark", "#232a38", "#83b4ff", "#f1f5fc", "#39465b", "#171b24"),
            ("light", "#ffffff", "#255caa", "#172133", "#dce4ef", "#f1f4f9"),
        ]:
            bridge.setTheme(mode)
            QTest.qWait(150)
            image = _logical_image(root, tmp_path / f"{mode}.png")
            assert (image.width(), image.height()) == (640, 360)
            _check_pixels(image, items, mode, surface, accent, text, track, window, qapp)
            _check_disabled(root, mode, text)
            _check_pill(root, items, tmp_path, mode)
            _check_segment_focus(root, items, tmp_path, mode)
            snapshots[mode] = image
        _check_navigation(root, bridge, items)
        assert snapshots["dark"].pixel(2, 2) != snapshots["light"].pixel(2, 2)
        assert warnings == []
    finally:
        bridge.shutdown()
        for root in engine.rootObjects():
            root.close()
        engine.deleteLater()


def test_basic_style_overrides_an_environment_style_in_a_fresh_process():
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from PySide6.QtWidgets import QApplication; "
            "from PySide6.QtQuickControls2 import QQuickStyle; "
            "from opticfilm_linux.app.qt.app import configure_style; "
            "app = QApplication([]); configure_style(); "
            "assert QQuickStyle.name() == 'Basic', QQuickStyle.name()",
        ],
        env={**os.environ, "QT_QUICK_CONTROLS_STYLE": "Fusion"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _check_disabled(root, mode, text):
    disabled = palette_colors(mode, disabled=True)
    assert root.property("disabledPaletteText").name() == disabled["Text"]
    assert root.property("inactivePaletteText").name() == text
    for name, role in [
        ("disabledButton", "ButtonText"),
        ("disabledLabel", "WindowText"),
        ("disabledMenuItem", "Text"),
    ]:
        item = root.findChild(QQuickItem, name)
        foreground = item if name == "disabledLabel" else item.property("contentItem")
        assert foreground.property("color").name() == disabled[role]


def _check_pill(root, items, tmp_path, mode):
    pill = items["Pill"]
    pill.forceActiveFocus(Qt.TabFocusReason)
    QTest.qWait(50)
    outer = _visual_child(pill, "pillFocusRing")
    inner = _visual_child(pill, "pillFocusInnerRing")
    assert outer.isVisible() and inner.isVisible()
    image = _logical_image(root, tmp_path / f"{mode}-pill-focus.png")
    assert _sample(image, pill, 80, 0) == root.property("themeText").name()
    assert _sample(image, pill, 80, 2) == root.property("accentText").name()
    for token in ("themeCard", "themeWindow", "themeTrack"):
        assert _contrast(root.property("themeText"), root.property(token)) >= 3
    for token in ("themeAccent", "pressedAccent"):
        assert _contrast(root.property("accentText"), root.property(token)) >= 4.5
    point = pill.mapToScene(QPointF(15, 22)).toPoint()
    QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, point)
    QTest.qWait(50)
    assert pill.property("down")
    assert pill.property("background").property("color") == root.property("pressedAccent")
    assert (
        _contrast(
            pill.property("contentItem").property("color"),
            pill.property("background").property("color"),
        )
        >= 4.5
    )
    image = _logical_image(root, tmp_path / f"{mode}-pill-pressed.png")
    assert _sample(image, pill, 15, 22) == root.property("pressedAccent").name()
    QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, point)
    pill.setFocus(False)


def _check_segment_focus(root, items, tmp_path, mode):
    segments = items["Segments"]
    selected = 0 if mode == "dark" else 1
    for index in (0, 1):
        segment = _visual_child(segments, f"segment{index}")
        segment.forceActiveFocus(Qt.TabFocusReason)
        QTest.qWait(30)
        image = _logical_image(root, tmp_path / f"{mode}-segment{index}-focus.png")
        outer = root.property("themeText")
        inner = root.property("accentText" if index == selected else "themeText")
        fill = root.property("themeAccent" if index == selected else "themeTrack")
        assert _contrast(inner, fill) >= 3
        assert _sample(image, segment, 60, 0) == outer.name()
        assert _sample(image, segment, 60, 2) == inner.name()
        # Check the square join, where a background child previously covered the border.
        x = 119 if index == 0 else 0
        edge = QColor(_sample(image, segment, x, 22))
        assert max(abs(a - b) for a, b in zip(edge.getRgb(), outer.getRgb(), strict=True)) <= 8
        segment.setFocus(False)


def _check_navigation(root, bridge, items):
    navigation = root.findChild(QQuickItem, "navigationSegments")
    _visual_child(navigation, "segment0").forceActiveFocus(Qt.TabFocusReason)
    for count, (key, index) in enumerate(
        [
            (Qt.Key_Left, 2),
            (Qt.Key_Right, 0),
            (Qt.Key_Up, 2),
            (Qt.Key_Down, 0),
            (Qt.Key_End, 2),
            (Qt.Key_Home, 0),
            (Qt.Key_Right, 1),
            (Qt.Key_Down, 2),
            (Qt.Key_Left, 1),
            (Qt.Key_Up, 0),
        ],
        1,
    ):
        QTest.keyClick(root, key)
        QTest.qWait(30)
        assert root.property("navigationIndex") == index
        assert navigation.property("currentIndex") == index
        assert root.property("activationCount") == count
        assert _visual_child(navigation, f"segment{index}").hasActiveFocus()
        for candidate in range(3):
            assert _visual_child(navigation, f"segment{candidate}").property("checked") == (
                candidate == index
            )
    root.setProperty("navigationIndex", 1)
    assert navigation.property("currentIndex") == 1
    theme_segments = items["Segments"]
    _visual_child(theme_segments, "segment1").forceActiveFocus(Qt.TabFocusReason)
    QTest.keyClick(root, Qt.Key_Right)
    QTest.qWait(30)
    assert bridge.theme == "dark"
    bridge.setTheme("light")
    assert theme_segments.property("currentIndex") == 1


def _check_pixels(image, items, mode, surface, accent, text, track, window, qapp):
    assert image.pixelColor(2, 2).name() == window
    assert _sample(image, items["Card"], 300, 280) == surface
    assert _sample(image, items["Pill"], 15, 22) == accent
    # Both selected ends and the straight join are painted by the real segments.
    selected_x = 20 if mode == "dark" else 220
    assert _sample(image, items["Segments"], selected_x, 22) == accent
    assert _sample(image, items["Segments"], 118 if mode == "dark" else 122, 4) == accent
    assert _sample(image, items["Segments"], 220 if mode == "dark" else 20, 22) == track
    slider_track = items["Slider"].property("background")
    assert _sample(image, slider_track, 10, slider_track.height() / 2) == accent
    indicator = items["Check"].property("indicator")
    assert _sample(image, indicator, 5, 5) == surface
    assert items["Heading"].property("color").name() == text
    assert qapp.palette().color(QPalette.Active, QPalette.Window).name() == window
