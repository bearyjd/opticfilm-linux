"""Screen sizing is independent of Qt and uses logical pixels."""

import pytest

from opticfilm_linux.app.qt.settings import QSettingsPrefs
from opticfilm_linux.app.qt.window_geometry import (
    SavedGeometry,
    initial_geometry,
    override_geometry,
    parse_window_size,
    resolve_override,
    safe_grid_unit,
)


@pytest.mark.parametrize(
    "available, expected",
    [
        ((1920, 1080), (1632, 918, False, 720, 540)),
        ((3840, 2160), (1800, 1080, False, 720, 540)),
        ((1280, 800), (1088, 680, True, 720, 540)),
        ((1024, 600), (870, 540, True, 720, 540)),
        ((800, 600), (720, 540, True, 720, 540)),
        ((640, 480), (640, 480, True, 640, 480)),
    ],
)
def test_first_run_fits_target_screen(available, expected):
    assert initial_geometry(*available, 18, None) == expected


def test_saved_geometry_clamps_after_monitor_change():
    saved = SavedGeometry(2000, 1400, True)
    assert initial_geometry(1280, 800, 18, saved) == (1264, 752, True, 720, 540)
    assert saved == SavedGeometry(2000, 1400, True)


def test_saved_maximized_wins_over_first_run_rule_on_large_screen():
    assert initial_geometry(1920, 1080, 18, SavedGeometry(900, 700, False))[2] is False
    assert initial_geometry(1920, 1080, 18, SavedGeometry(900, 700, True))[2] is True
    assert initial_geometry(800, 600, 18, SavedGeometry(900, 700, False))[2] is False


@pytest.mark.parametrize("bad", [None, 0, -5, float("nan"), float("inf"), "x", True])
def test_bad_grid_unit_falls_back(bad):
    assert safe_grid_unit(bad) == 18
    assert initial_geometry(1920, 1080, bad, None) == initial_geometry(1920, 1080, 18, None)


@pytest.mark.parametrize("avail", [(0, 0), (-5, 10), (1, 1)])
@pytest.mark.parametrize("saved", [None, SavedGeometry(0, -3, False)])
def test_geometry_is_never_zero_or_negative(avail, saved):
    width, height, _max, min_w, min_h = initial_geometry(*avail, 0, saved)
    assert min(width, height, min_w, min_h) >= 1


@pytest.mark.parametrize("size", [(0, 0), (-1, 5), (1.5, 2), ("a", "b"), None, (1,), (True, 5)])
def test_invalid_override_size_is_rejected(size):
    assert override_geometry(size, 1920, 1080, 18) is None
    assert resolve_override(size, "") is None
    assert resolve_override(size, "900x650") == (900, 650)


def test_override_is_clamped_to_work_area_with_minimums():
    assert override_geometry((5000, 4000), 1920, 1080, 18) == (1920, 1080, 720, 540)
    assert resolve_override((1220, 780), "900x650") == (1220, 780)


def test_saved_windowed_choice_is_restored_on_small_screen():
    assert initial_geometry(800, 600, 18, SavedGeometry(400, 300, False)) == (
        720,
        540,
        False,
        720,
        540,
    )


def test_grid_unit_scales_thresholds_and_minimums():
    assert initial_geometry(1920, 1080, 30, None) == (1632, 918, True, 1200, 900)


def test_first_run_maximize_thresholds_are_strict():
    assert initial_geometry(1260, 810, 18, None)[2] is False
    assert initial_geometry(1259, 810, 18, None)[2] is True
    assert initial_geometry(1260, 809, 18, None)[2] is True


@pytest.mark.parametrize("value", ["", "800", "0x600", "800x-1", "800x600x2", "wrong"])
def test_invalid_environment_size_is_ignored(value):
    assert parse_window_size(value) is None


def test_environment_size_accepts_positive_logical_pixels():
    assert parse_window_size("1024x600") == (1024, 600)


class StoredSettings:
    def __init__(self, values=None):
        self.values = values or {}
        self.syncs = 0

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value

    def sync(self):
        self.syncs += 1


def test_window_settings_round_trip_only_dimensions_and_state():
    settings = StoredSettings()
    prefs = QSettingsPrefs(settings)
    assert prefs.window_geometry() is None
    prefs.save_window_geometry(1400, 900, True)
    assert prefs.window_geometry() == SavedGeometry(1400, 900, True)
    assert settings.values == {
        "window/width": 1400,
        "window/height": 900,
        "window/maximized": "true",
    }
    assert settings.syncs == 1


@pytest.mark.parametrize("width,height", [("bad", "600"), ("800", "0"), (None, "600")])
def test_corrupt_window_settings_fall_back_to_first_run(width, height):
    prefs = QSettingsPrefs(StoredSettings({"window/width": width, "window/height": height}))
    assert prefs.window_geometry() is None


@pytest.fixture
def qt_window(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.delenv("OPTICFILM_WINDOW_SIZE", raising=False)
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from opticfilm_linux.app.controller import Controller
    from opticfilm_linux.app.prefs import MemoryPrefs
    from opticfilm_linux.app.qt import settings as settings_module
    from opticfilm_linux.app.qt.app import create_engine, scanner_factory
    from opticfilm_linux.app.qt.bridge import Bridge
    from opticfilm_linux.app.qt.images import PreviewProvider

    app = QApplication.instance() or QApplication(["geometry"])
    stored = StoredSettings()
    monkeypatch.setattr(settings_module, "QSettingsPrefs", lambda: QSettingsPrefs(stored))
    bridge = Bridge(Controller(scanner_factory(None)), MemoryPrefs())
    engines = []

    def create(size=None):
        engine, warnings = create_engine(bridge, PreviewProvider(lambda *_: None), size=size)
        engines.append(engine)
        assert engine.rootObjects(), warnings
        app.processEvents()
        assert warnings == []
        return engine.rootObjects()[0]

    yield app, create, stored
    bridge.shutdown()
    for engine in engines:
        engine.deleteLater()
    app.processEvents()


@pytest.mark.qt
def test_engine_restores_clamped_geometry_and_saves_on_close(qt_window):
    app, create, stored = qt_window
    stored.values = {"window/width": 10000, "window/height": 10000}
    window = create()
    available = window.screen().availableGeometry()
    assert window.width() == available.width() - 16
    assert window.height() == available.height() - 48
    assert window.minimumWidth() <= available.width()
    window.resize(window.minimumWidth(), window.minimumHeight())
    app.processEvents()
    assert window.close()
    assert QSettingsPrefs(stored).window_geometry() == SavedGeometry(
        window.width(), window.height(), False
    )


@pytest.mark.qt
def test_engine_maximized_start_preserves_windowed_dimensions(qt_window):
    _app, create, stored = qt_window
    window = create()
    available = window.screen().availableGeometry()
    width, height, maximized, *_ = initial_geometry(
        available.width(), available.height(), window.property("gridUnit"), None
    )
    assert window.close()
    assert QSettingsPrefs(stored).window_geometry() == SavedGeometry(width, height, maximized)


@pytest.mark.qt
def test_explicit_engine_size_wins_over_environment_and_saved_size(qt_window, monkeypatch):
    _app, create, stored = qt_window
    stored.values = {"window/width": 400, "window/height": 300, "window/maximized": "true"}
    monkeypatch.setenv("OPTICFILM_WINDOW_SIZE", "780x600")
    window = create((760, 650))
    assert (window.width(), window.height()) == (760, 650)
    assert window.close()
    assert stored.syncs == 0


@pytest.mark.qt
def test_environment_engine_size_overrides_saved_geometry(qt_window, monkeypatch):
    _app, create, stored = qt_window
    monkeypatch.setenv("OPTICFILM_WINDOW_SIZE", "780x600")
    window = create()
    assert (window.width(), window.height()) == (780, 600)
    assert window.close()
    assert stored.syncs == 0


@pytest.mark.qt
def test_maximize_and_restore_remember_the_last_windowed_size(qt_window):
    app, create, stored = qt_window
    stored.values = {"window/width": 740, "window/height": 560}
    window = create()
    window.resize(760, 580)
    app.processEvents()
    window.showMaximized()
    app.processEvents()
    assert window.close()
    assert QSettingsPrefs(stored).window_geometry() == SavedGeometry(760, 580, True)
    restored = create()
    restored.showNormal()
    app.processEvents()
    assert (restored.width(), restored.height()) == (760, 580)
    assert restored.close()
    assert QSettingsPrefs(stored).window_geometry() == SavedGeometry(760, 580, False)


@pytest.mark.qt
def test_invalid_explicit_size_still_shows_window(qt_window):
    _app, create, _stored = qt_window
    window = create((0, 0))
    assert window.isVisible()
    assert window.width() > 0 and window.height() > 0


@pytest.mark.qt
def test_geometry_failure_still_shows_default_window(qt_window, monkeypatch):
    from opticfilm_linux.app.qt import app as app_module

    def boom(*_args):
        raise RuntimeError("boom")

    monkeypatch.setattr(app_module, "initial_geometry", boom)
    _app, create, _stored = qt_window
    window = create()
    assert window.isVisible()
    assert window.width() > 0 and window.height() > 0


@pytest.mark.qt
def test_maximized_size_is_never_stored_as_windowed(qt_window):
    app, create, _stored = qt_window
    window = create()
    window.showNormal()
    app.processEvents()
    window.resize(750, 570)
    app.processEvents()
    window.showMaximized()
    app.processEvents()
    assert (window.property("windowedWidth"), window.property("windowedHeight")) == (750, 570)


@pytest.mark.qt
def test_rejected_close_does_not_persist_geometry(qt_window, monkeypatch):
    from opticfilm_linux.app.controller import Controller

    _app, create, stored = qt_window
    window = create()
    with monkeypatch.context() as patch:
        patch.setattr(Controller, "shutdown_decision", lambda _self: "scan_running")
        assert not window.close()
        assert stored.syncs == 0
    assert window.close()
    assert stored.syncs == 1
