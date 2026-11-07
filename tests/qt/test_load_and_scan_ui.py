"""Load & scan in the window: the setting, the control, Enter, the phase text and the close wait.

Run with: QT_QPA_PLATFORM=offscreen pytest -m qt tests/qt/test_load_and_scan_ui.py
"""
# ruff: noqa: F811

import dataclasses
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = pytest.mark.qt
pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from test_qt_smoke import _wait, qapp  # noqa: E402, F401

from opticfilm_linux.app import prefs as preferences  # noqa: E402
from opticfilm_linux.app.controller import Controller  # noqa: E402
from opticfilm_linux.app.prefs import MemoryPrefs  # noqa: E402
from opticfilm_linux.app.qt.app import create_engine  # noqa: E402
from opticfilm_linux.app.qt.bridge import Bridge  # noqa: E402
from opticfilm_linux.app.qt.images import PreviewProvider  # noqa: E402
from opticfilm_linux.app.workflow_settings import startup_switches  # noqa: E402
from opticfilm_linux.scanner.base import ScannerStatus  # noqa: E402
from opticfilm_linux.scanner.fake import FakeScanner  # noqa: E402

ENV = "OPTICFILM_LOAD_AND_SCAN"
LABEL = "Holder latched – Load & scan"


class LoadScanner(FakeScanner):
    """READY until ejected, NOT_READY until loaded; a load turns it READY unless held back."""

    def __init__(self, sweep_dir):
        super().__init__(sweep_dir)
        self.usb_address = (1, 2)
        self.calls: list[str] = []
        self.current = ScannerStatus.READY
        self.after_load = ScannerStatus.READY
        self.scans = 0

    def status(self):
        return self.current

    def eject(self):
        self.calls.append("eject")
        self.eject_outcome = "ejected"
        self.current = ScannerStatus.NOT_READY

    def load_next_strip(self):
        self.calls.append("load")
        super().load_next_strip()
        self.current = self.after_load

    def scan_holder(self, settings):
        self.scans += 1
        return super().scan_holder(settings)


@pytest.fixture
def window(qapp, sweep600_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))  # the marker and latch files
    for name in (ENV, "OPTICFILM_NEXT_STRIP", "OPTICFILM_AUTO_EJECT", "OPTICFILM_SKIP_EMPTY"):
        monkeypatch.delenv(name, raising=False)
    made = []

    def build(prefs=None, **env):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        prefs = prefs or MemoryPrefs()
        scanner = LoadScanner(sweep600_dir)
        controller = Controller(lambda _dpi: scanner, next_strip=True, **startup_switches(prefs))
        bridge = Bridge(controller, prefs, demo_dpi=600)
        provider = PreviewProvider(lambda n, p, k: controller.preview_path(n, p, k))
        engine, warnings = create_engine(bridge, provider, size=(1220, 780))
        made.append((bridge, engine, warnings))
        batch_dir = tmp_path / f"b{len(made)}"
        batch_dir.mkdir()
        parent = QUrl.fromLocalFile(str(batch_dir)).toString()
        bridge.newBatch(parent, "LS", "", "", 600)
        _wait(bridge, lambda: bridge.hasBatch and bridge.canScan)
        root = engine.rootObjects()[0]
        root.findChild(QQuickItem, "mainPage").forceActiveFocus()
        QTest.qWait(50)
        return bridge, root, scanner, prefs

    yield build
    for bridge, engine, warnings in made:
        assert warnings == []
        bridge.shutdown()
        engine.deleteLater()


def _item(root, name):
    item = root.findChild(QObject, name)
    assert item is not None, name
    return item


def _visible(root, name):
    item = root.findChild(QObject, name)
    return item is not None and bool(item.property("visible"))


def _enter(root, key=Qt.Key.Key_Return, repeat=False, modifiers=Qt.KeyboardModifier.NoModifier):
    event = QKeyEvent(QEvent.Type.KeyPress, key, modifiers, "\r", repeat)
    QCoreApplication.sendEvent(root, event)
    QTest.qWait(30)


def _force(bridge, **fields):
    """Pin the state (the poll timer is stopped so nothing overwrites it)."""
    bridge._timer.stop()
    bridge._state = dataclasses.replace(bridge._state, **fields)
    bridge.changed.emit()
    QTest.qWait(30)


def _record(bridge, monkeypatch):
    log: list[str] = []
    controller = bridge._controller
    monkeypatch.setattr(controller, "load_and_scan", lambda: log.append("load_and_scan"))
    monkeypatch.setattr(controller, "scan", lambda: log.append("scan"))
    monkeypatch.setattr(controller, "load_next_holder", lambda: log.append("load"))
    monkeypatch.setattr(controller, "finish_box", lambda next_box: log.append("finish"))
    return log


def _released(bridge, scanner):
    """Eject in the app, so the chain may start (RELEASED + a fresh NOT_READY)."""
    bridge.ejectHolder()
    _wait(
        bridge, lambda: scanner.calls == ["eject"] and bridge.holderLatchText.endswith("released")
    )
    scanner.calls.clear()


# -- off by default ---------------------------------------------------------------------------


def test_off_on_a_fresh_profile_hides_the_control_and_enter_does_nothing(window):
    bridge, root, scanner, prefs = window()
    assert bridge.loadAndScanEnabled is False
    assert preferences.read_switch(prefs, preferences.LOAD_AND_SCAN_KEY) is False
    _released(bridge, scanner)
    assert not bridge.canLoadAndScan
    assert not _visible(root, "loadAndScanButton")
    assert _item(root, "primaryActionButton").property("text") != LABEL
    _enter(root)
    _enter(root, Qt.Key.Key_Enter, modifiers=Qt.KeyboardModifier.KeypadModifier)
    QTest.qWait(300)
    assert scanner.calls == []
    assert scanner.load_calls == 0


def test_the_switch_saves_the_setting_and_shows_the_control_live(window):
    bridge, root, scanner, prefs = window()
    _released(bridge, scanner)
    bridge.setLoadAndScan(True)
    assert preferences.read_switch(prefs, preferences.LOAD_AND_SCAN_KEY) is True
    _wait(bridge, lambda: bridge.canLoadAndScan)
    assert _visible(root, "loadAndScanButton")
    assert _item(root, "loadAndScanButton").property("enabled")
    assert _item(root, "primaryActionButton").property("text") == LABEL
    bridge.setLoadAndScan(False)
    assert preferences.read_switch(prefs, preferences.LOAD_AND_SCAN_KEY) is False
    _wait(bridge, lambda: not bridge.canLoadAndScan)
    assert not _visible(root, "loadAndScanButton")


def test_a_saved_setting_is_used_at_the_next_start(window):
    prefs = MemoryPrefs()
    preferences.remember_switch(prefs, preferences.LOAD_AND_SCAN_KEY, True)
    bridge, root, _scanner, _prefs = window(prefs)
    assert bridge.loadAndScanEnabled is True
    assert _visible(root, "loadAndScanButton")
    assert not _item(root, "loadAndScanButton").property("enabled")  # not RELEASED yet


@pytest.mark.parametrize(("value", "on"), [("1", True), ("0", False)])
def test_the_environment_decides_and_locks_the_switch(window, value, on):
    prefs = MemoryPrefs()
    preferences.remember_switch(prefs, preferences.LOAD_AND_SCAN_KEY, not on)
    bridge, root, _scanner, _prefs = window(prefs, OPTICFILM_LOAD_AND_SCAN=value)
    assert bridge.loadAndScanEnabled is on
    assert bridge.loadAndScanLockedBy == ENV
    bridge.setLoadAndScan(not on)
    assert bridge.loadAndScanEnabled is on
    assert preferences.read_switch(prefs, preferences.LOAD_AND_SCAN_KEY) is (not on)  # unchanged
    root.findChild(QObject, "workflowSettingsDialog").open()
    QTest.qWait(100)
    assert not _item(root, "loadAndScanSwitch").property("enabled")
    assert _visible(root, "loadAndScanSettingLocked")


# -- Enter --------------------------------------------------------------------------------------


def test_enter_with_the_setting_off_does_nothing_even_if_the_gate_says_yes(window, monkeypatch):
    bridge, root, _scanner, _prefs = window()
    log = _record(bridge, monkeypatch)
    _force(bridge, can_load_and_scan=True)
    _enter(root)
    assert log == []


@pytest.mark.parametrize(
    ("key", "modifiers"),
    [
        (Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier),
        (Qt.Key.Key_Enter, Qt.KeyboardModifier.KeypadModifier),
    ],
)
def test_enter_starts_load_and_scan_once_when_its_gate_is_open(window, monkeypatch, key, modifiers):
    bridge, root, _scanner, _prefs = window()
    bridge.setLoadAndScan(True)
    log = _record(bridge, monkeypatch)
    _force(bridge, can_load_and_scan=True, can_scan=False, can_load_next=True)
    _enter(root, key, modifiers=modifiers)
    assert log == ["load_and_scan"]


def test_enter_never_scans_or_loads_on_its_own(window, monkeypatch):
    bridge, root, _scanner, _prefs = window()
    bridge.setLoadAndScan(True)
    log = _record(bridge, monkeypatch)
    _force(bridge, can_load_and_scan=False, can_scan=True, can_load_next=True, can_first_load=True)
    _enter(root)
    assert log == []


@pytest.mark.parametrize(
    "fields",
    [{"can_load_and_scan": False}, {"readonly": True}],
    ids=["gate closed", "read-only"],
)
def test_enter_does_nothing_when_refused(window, monkeypatch, fields):
    bridge, root, _scanner, _prefs = window()
    bridge.setLoadAndScan(True)
    log = _record(bridge, monkeypatch)
    _force(bridge, **{"can_load_and_scan": True, **fields})
    _enter(root)
    assert log == []


def test_a_held_enter_does_not_repeat(window, monkeypatch):
    bridge, root, _scanner, _prefs = window()
    bridge.setLoadAndScan(True)
    log = _record(bridge, monkeypatch)
    _force(bridge, can_load_and_scan=True)
    _enter(root, repeat=True)
    assert log == []


def test_enter_in_a_text_field_does_nothing(window, monkeypatch):
    bridge, root, _scanner, _prefs = window()
    bridge.setLoadAndScan(True)
    log = _record(bridge, monkeypatch)
    _force(bridge, can_load_and_scan=True)
    field = root.findChild(QQuickItem, "metaDescription")
    assert field is not None
    field.forceActiveFocus()
    QTest.qWait(30)
    _enter(root)
    assert log == []


@pytest.mark.parametrize("name", ["ejectButton", "primaryActionButton", "loadAndScanButton"])
def test_enter_on_a_focused_button_does_not_start_load_and_scan(window, monkeypatch, name):
    """Tab to Eject, then Enter: the key is about that button, never a motor chain."""
    bridge, root, _scanner, _prefs = window()
    bridge.setLoadAndScan(True)
    log = _record(bridge, monkeypatch)
    monkeypatch.setattr(bridge._controller, "eject_holder", lambda: log.append("eject"))
    _force(bridge, can_load_and_scan=True, can_eject=True)
    button = root.findChild(QQuickItem, name)
    assert button is not None
    button.forceActiveFocus()
    QTest.qWait(30)
    assert button.hasActiveFocus()
    _enter(root)
    _enter(root, Qt.Key.Key_Enter, modifiers=Qt.KeyboardModifier.KeypadModifier)
    assert "load_and_scan" not in log


def test_enter_that_answers_a_dialog_does_not_start_load_and_scan(window, monkeypatch):
    bridge, root, _scanner, _prefs = window()
    bridge.setLoadAndScan(True)
    log = _record(bridge, monkeypatch)
    _force(bridge, can_load_and_scan=True, can_finish_box=True)
    dialog = _item(root, "finishBoxDialog")
    dialog.open()
    _wait(bridge, lambda: dialog.property("opened"), label="finish dialog")
    QTest.keyClick(root, Qt.Key.Key_Return)  # answers the dialog (its focused Cancel)
    QTest.qWait(100)
    assert not dialog.property("opened")
    assert log == []


# -- phases ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("phase", "pending", "text"),
    [
        ("loading", True, "Loading the holder…"),
        ("waiting_ready", True, "Waiting for the scanner…"),
        ("scanning", True, "Scanning…"),
    ],
)
def test_the_primary_button_shows_the_phase(window, phase, pending, text):
    bridge, root, _scanner, _prefs = window()
    bridge.setLoadAndScan(True)
    _force(bridge, load_scan_phase=phase, scan_pending=pending, can_scan=False, holder_busy=True)
    assert _item(root, "primaryActionButton").property("text") == text
    assert not _item(root, "primaryActionButton").property("enabled")
    assert _visible(root, "loadScanPhaseText")


def test_a_failed_chain_says_nothing_was_scanned(window):
    bridge, root, _scanner, _prefs = window()
    bridge.setLoadAndScan(True)
    _force(bridge, load_scan_phase="failed")
    assert "nothing was scanned" in _item(root, "loadScanPhaseText").property("text")


def test_no_phase_text_while_idle_or_switched_off(window):
    bridge, root, _scanner, _prefs = window()
    assert not _visible(root, "loadScanPhaseText")
    _force(bridge, load_scan_phase="failed")  # a left-over phase with the setting off
    assert not _visible(root, "loadScanPhaseText")


# -- on the fake scanner ---------------------------------------------------------------------


def test_eject_then_enter_loads_once_and_scans_once(window):
    bridge, root, scanner, _prefs = window()
    bridge.setLoadAndScan(True)
    _released(bridge, scanner)
    _wait(bridge, lambda: bridge.canLoadAndScan)
    _enter(root)
    _wait(bridge, lambda: scanner.scans == 1 and bridge.loadScanPhase == "idle", timeout=20)
    assert scanner.calls == ["load"]
    assert scanner.load_calls == 1
    _wait(bridge, lambda: not bridge.scanPending, timeout=20)
    assert not bridge.canLoadAndScan  # once per eject


def test_wait_then_close_during_the_load_wait_does_not_reopen_and_closes_after(window):
    bridge, root, scanner, _prefs = window()
    bridge.setLoadAndScan(True)
    _released(bridge, scanner)
    scanner.after_load = ScannerStatus.NOT_READY  # held in waiting_ready
    _wait(bridge, lambda: bridge.canLoadAndScan)
    bridge.loadAndScan()
    _wait(bridge, lambda: bridge.loadScanPhase == "waiting_ready")
    root.close()
    QTest.qWait(50)
    wait_dialog = _item(root, "waitDialog")
    assert wait_dialog.property("visible")
    root.setProperty("closeWhenIdle", True)  # "Wait, then close"
    wait_dialog.close()
    for _ in range(10):  # several polls during the wait
        bridge.poll()
        QTest.qWait(50)
        assert not wait_dialog.property("visible") or not wait_dialog.property("opened")
    assert root.isVisible()
    assert scanner.scans == 0
    scanner.current = ScannerStatus.READY
    _wait(bridge, lambda: not root.isVisible(), timeout=20)
    assert scanner.scans == 1
