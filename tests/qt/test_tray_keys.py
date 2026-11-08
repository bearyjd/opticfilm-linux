"""A whole tray from the keyboard: S scan, E eject, L load next, X export, F finish box."""
# ruff: noqa: F811

import dataclasses
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = pytest.mark.qt
pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from test_qt_smoke import _wait, qapp  # noqa: E402, F401

from opticfilm_linux.app.controller import Controller  # noqa: E402
from opticfilm_linux.app.prefs import MemoryPrefs  # noqa: E402
from opticfilm_linux.app.qt.app import create_engine  # noqa: E402
from opticfilm_linux.app.qt.bridge import Bridge  # noqa: E402
from opticfilm_linux.app.qt.images import PreviewProvider  # noqa: E402
from opticfilm_linux.app.state import Banner  # noqa: E402
from opticfilm_linux.scanner.base import ScannerStatus  # noqa: E402
from opticfilm_linux.scanner.fake import FakeScanner  # noqa: E402

KEYS = {
    "scan": Qt.Key.Key_S,
    "eject": Qt.Key.Key_E,
    "load": Qt.Key.Key_L,
    "export": Qt.Key.Key_X,
    "finish": Qt.Key.Key_F,
}


class TrayScanner(FakeScanner):
    """READY until ejected, NOT_READY until the next strip is loaded; records the moves."""

    def __init__(self, sweep_dir, calls):
        super().__init__(sweep_dir)
        self.calls = calls
        self._loaded = True

    def status(self):
        return ScannerStatus.READY if self._loaded else ScannerStatus.NOT_READY

    def eject(self):
        self.calls.append("eject")
        self._loaded = False

    def load_next_strip(self):
        self.calls.append("load")
        self._loaded = True


@pytest.fixture
def tray(qapp, sweep600_dir, tmp_path, monkeypatch):
    monkeypatch.delenv("OPTICFILM_NEXT_STRIP", raising=False)
    calls: list[str] = []
    controller = Controller(lambda _dpi: TrayScanner(sweep600_dir, calls), next_strip=True)
    bridge = Bridge(controller, MemoryPrefs(), demo_dpi=600)
    provider = PreviewProvider(lambda n, p, k: controller.preview_path(n, p, k))
    engine, warnings = create_engine(bridge, provider, size=(1220, 780))
    parent = QUrl.fromLocalFile(str(tmp_path)).toString()
    bridge.newBatch(parent, "Keys", "", "", 600, collection="Coll")
    _wait(bridge, lambda: bridge.hasBatch and bridge.canScan)
    root = engine.rootObjects()[0]
    page = root.findChild(QQuickItem, "mainPage")
    page.forceActiveFocus()
    QTest.qWait(100)
    yield bridge, root, calls, warnings
    assert warnings == []
    bridge.shutdown()
    engine.deleteLater()


def _press(root, name, repeat=False, modifiers=Qt.KeyboardModifier.NoModifier):
    key = KEYS[name]
    text = chr(key).lower()
    event = QKeyEvent(QEvent.Type.KeyPress, key, modifiers, text, repeat)
    QCoreApplication.sendEvent(root, event)
    QTest.qWait(30)


def _refocus(root):
    """A closed dialog hands focus back through the app; the offscreen window needs a nudge."""
    root.findChild(QQuickItem, "mainPage").forceActiveFocus()
    QTest.qWait(30)


def _record(bridge, monkeypatch):
    """Replace the controller entry points the keys must reach; return the call log."""
    log: list[str] = []
    controller = bridge._controller
    monkeypatch.setattr(controller, "scan", lambda: log.append("scan"))
    monkeypatch.setattr(controller, "eject_holder", lambda: log.append("eject"))
    monkeypatch.setattr(controller, "load_next_holder", lambda: log.append("load"))
    monkeypatch.setattr(controller, "finish_box", lambda next_box: log.append(f"finish{next_box}"))
    return log


def _force(bridge, **flags):
    """Pin the gates (the poll timer is stopped so nothing overwrites them)."""
    bridge._timer.stop()
    bridge._state = dataclasses.replace(bridge._state, **flags)
    bridge.changed.emit()
    QTest.qWait(30)


def _dialog(root, name):
    item = root.findChild(QObject, name)
    assert item is not None, name
    return item


def _open_count(root, *names):
    return [bool(_dialog(root, n).property("visible")) for n in names]


def test_each_key_calls_its_slot_once_when_its_gate_is_open(tray, monkeypatch):
    bridge, root, _calls, _warnings = tray
    log = _record(bridge, monkeypatch)
    _force(
        bridge,
        can_scan=True,
        can_eject=True,
        can_load_next=True,
        can_finish_box=True,
        readonly=False,
    )
    for name in ("scan", "eject", "load"):
        _press(root, name)
    assert log == ["scan", "eject", "load"]


def test_export_and_finish_keys_only_open_their_dialogs(tray, monkeypatch):
    bridge, root, _calls, _warnings = tray
    log = _record(bridge, monkeypatch)
    _force(bridge, can_finish_box=True, readonly=False)
    export = _dialog(root, "exportDialog")
    finish = _dialog(root, "finishBoxDialog")
    bridge._trays.set_rows([{"number": 1}])  # a tray exists: the Export action is enabled
    QTest.qWait(30)
    _press(root, "export")
    _wait(bridge, lambda: export.property("opened"), label="export dialog")
    assert log == []
    export.close()
    _wait(bridge, lambda: not export.property("opened"))
    _refocus(root)
    _press(root, "finish")
    _wait(bridge, lambda: finish.property("opened"), label="finish dialog")
    assert log == []  # nothing happens until the owner confirms
    QMetaObject.invokeMethod(finish, "accept")  # the Yes button
    _wait(bridge, lambda: log == ["finishTrue"], label="confirmed finish")


@pytest.mark.parametrize("name", ["scan", "eject", "load", "export", "finish"])
def test_a_key_does_nothing_when_its_button_is_disabled(tray, monkeypatch, name):
    bridge, root, _calls, _warnings = tray
    log = _record(bridge, monkeypatch)
    _force(
        bridge,
        can_scan=False,
        can_eject=False,
        can_load_next=False,
        can_finish_box=False,
        readonly=False,
    )
    bridge._trays.set_rows([])
    QTest.qWait(30)
    _press(root, name)
    assert log == []
    assert _open_count(root, "exportDialog", "finishBoxDialog") == [False, False]


@pytest.mark.parametrize("name", ["scan", "eject", "load", "export", "finish"])
def test_a_readonly_batch_ignores_every_key(tray, monkeypatch, name):
    bridge, root, _calls, _warnings = tray
    log = _record(bridge, monkeypatch)
    _force(
        bridge,
        can_scan=True,
        can_eject=True,
        can_load_next=True,
        can_finish_box=True,
        readonly=True,
    )
    bridge._trays.set_rows([{"number": 1}])
    QTest.qWait(30)
    _press(root, name)
    assert log == []
    assert _open_count(root, "exportDialog", "finishBoxDialog") == [False, False]


@pytest.mark.parametrize("name", ["scan", "eject", "load", "export", "finish"])
def test_auto_repeat_is_ignored(tray, monkeypatch, name):
    bridge, root, _calls, _warnings = tray
    log = _record(bridge, monkeypatch)
    _force(
        bridge,
        can_scan=True,
        can_eject=True,
        can_load_next=True,
        can_finish_box=True,
        readonly=False,
    )
    bridge._trays.set_rows([{"number": 1}])
    QTest.qWait(30)
    _press(root, name, repeat=True)
    assert log == []
    assert _open_count(root, "exportDialog", "finishBoxDialog") == [False, False]


@pytest.mark.parametrize("name", ["scan", "eject", "load", "export", "finish"])
def test_modified_letters_are_not_the_motor_keys(tray, monkeypatch, name):
    bridge, root, _calls, _warnings = tray
    log = _record(bridge, monkeypatch)
    _force(bridge, can_scan=True, can_eject=True, can_load_next=True, can_finish_box=True)
    bridge._trays.set_rows([{"number": 1}])
    QTest.qWait(30)
    for modifier in (
        Qt.KeyboardModifier.ControlModifier,
        Qt.KeyboardModifier.AltModifier,
        Qt.KeyboardModifier.ShiftModifier,
        Qt.KeyboardModifier.MetaModifier,
    ):
        _press(root, name, modifiers=modifier)
    assert log == []
    assert _open_count(root, "exportDialog", "finishBoxDialog") == [False, False]


def test_f_without_a_collection_does_nothing(tray, monkeypatch):
    bridge, root, _calls, _warnings = tray
    log = _record(bridge, monkeypatch)
    _force(bridge, can_finish_box=True, readonly=False, collection="")
    _press(root, "finish")
    assert log == []
    assert _open_count(root, "finishBoxDialog") == [False]


@pytest.mark.parametrize("name", ["scan", "eject", "load", "finish"])
def test_keys_do_nothing_while_a_modal_dialog_is_open(tray, monkeypatch, name):
    bridge, root, _calls, _warnings = tray
    log = _record(bridge, monkeypatch)
    _force(
        bridge,
        can_scan=True,
        can_eject=True,
        can_load_next=True,
        can_finish_box=True,
        readonly=False,
    )
    bridge._trays.set_rows([{"number": 1}])
    QTest.qWait(30)
    export = _dialog(root, "exportDialog")
    _press(root, "export")
    _wait(bridge, lambda: export.property("opened"), label="export dialog")
    _press(root, name)
    assert log == []
    assert _open_count(root, "finishBoxDialog") == [False]


def test_held_review_keys_still_repeat(tray, monkeypatch):
    bridge, root, _calls, _warnings = tray
    seen: list[str] = []
    monkeypatch.setattr(bridge._controller, "move_focus", lambda *a: seen.append("move"))
    monkeypatch.setattr(
        bridge._controller, "toggle_selection_accepted", lambda: seen.append("space")
    )
    _force(bridge, readonly=False)
    for key in (Qt.Key.Key_Space, Qt.Key.Key_Right):
        event = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, "", True)
        QCoreApplication.sendEvent(root, event)
    QTest.qWait(30)
    assert seen == ["space", "move"]


def test_f_then_enter_or_space_does_not_finish_the_box(tray, monkeypatch):
    bridge, root, _calls, _warnings = tray
    log = _record(bridge, monkeypatch)
    _force(bridge, can_finish_box=True, readonly=False)
    finish = _dialog(root, "finishBoxDialog")
    _press(root, "finish")
    _wait(bridge, lambda: finish.property("opened"), label="finish dialog")
    cancel = finish.findChild(QQuickItem, "confirmCancel")
    yes = finish.findChild(QQuickItem, "confirmAccept")
    _wait(bridge, lambda: cancel.property("activeFocus"), label="Cancel focused")
    assert not yes.property("activeFocus")
    QTest.keyClick(root, Qt.Key.Key_Space)
    QTest.qWait(50)
    assert log == []
    finish.open()
    QTest.keyClick(root, Qt.Key.Key_Return)
    QTest.qWait(50)
    assert log == []
    assert not finish.property("opened")  # Enter on the focused Cancel closes it


def test_an_explicit_yes_still_finishes_the_box(tray, monkeypatch):
    bridge, root, _calls, _warnings = tray
    log = _record(bridge, monkeypatch)
    _force(bridge, can_finish_box=True, readonly=False)
    finish = _dialog(root, "finishBoxDialog")
    _press(root, "finish")
    _wait(bridge, lambda: finish.property("opened"), label="finish dialog")
    yes = finish.findChild(QQuickItem, "confirmAccept")
    QMetaObject.invokeMethod(yes, "click")
    _wait(bridge, lambda: log == ["finishTrue"], label="confirmed finish")


def test_keys_typed_into_a_text_field_do_nothing(tray, monkeypatch):
    bridge, root, _calls, _warnings = tray
    log = _record(bridge, monkeypatch)
    _force(bridge, can_scan=True, can_eject=True, can_load_next=True, can_finish_box=True)
    field = root.findChild(QQuickItem, "metaDescription")
    assert field is not None
    field.forceActiveFocus()
    QTest.qWait(30)
    for name in KEYS:
        _press(root, name)
    assert log == []


EXISTING_KEYS = [
    (Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier, "select_all"),
    (Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier, "undo"),
    (Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier, "move_focus"),
    (Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier, "move_focus"),
    (Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier, "move_focus"),
    (Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier, "move_focus"),
    (Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier, "clear_selection"),
    (Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, "toggle_selection_accepted"),
    (Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier, "set_selection_status"),
    (Qt.Key.Key_R, Qt.KeyboardModifier.NoModifier, "set_selection_status"),
    (Qt.Key.Key_N, Qt.KeyboardModifier.NoModifier, "set_selection_status"),
    (Qt.Key.Key_BracketLeft, Qt.KeyboardModifier.NoModifier, "rotate_selection"),
    (Qt.Key.Key_BracketRight, Qt.KeyboardModifier.NoModifier, "rotate_selection"),
    (Qt.Key.Key_M, Qt.KeyboardModifier.NoModifier, "mirror_selection"),
]


@pytest.mark.parametrize(("key", "modifiers", "method"), EXISTING_KEYS)
def test_existing_review_keys_still_reach_their_controller_call(
    tray, monkeypatch, key, modifiers, method
):
    bridge, root, _calls, _warnings = tray
    seen: list[str] = []
    monkeypatch.setattr(bridge._controller, method, lambda *a, **k: seen.append(method))
    _force(bridge, readonly=False)
    QTest.keyClick(root, key, modifiers)
    assert seen == [method]


def test_a_whole_tray_runs_from_the_keyboard(tray):
    bridge, root, calls, _warnings = tray
    _press(root, "scan")
    _wait(bridge, lambda: bridge.trays.rowCount() == 1 and not bridge.scanPending)
    _wait(
        bridge,
        lambda: all(s["selectable"] for s in bridge.sheet.get(0)["slides"]),
        label="the scanned tray to become selectable",
    )
    _wait(bridge, lambda: bridge._controller._selection_order, label="reviewable slides")
    _refocus(root)
    QTest.keyClick(root, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    _wait(bridge, lambda: len(bridge.selection) == 4)
    QTest.keyClick(root, Qt.Key.Key_A)
    export = _dialog(root, "exportDialog")
    _press(root, "export")
    _wait(bridge, lambda: export.property("opened"), label="export dialog")
    export.close()
    _wait(bridge, lambda: not export.property("opened"))
    _refocus(root)
    _wait(bridge, lambda: bridge.canEject)
    _press(root, "eject")
    _wait(bridge, lambda: calls == ["eject"] and bridge.canLoadNext)
    assert not bridge.canScan  # the released holder blocks a scan: the key must not start one
    _press(root, "scan")
    QTest.qWait(100)
    assert not bridge.scanPending
    _press(root, "load")
    _wait(bridge, lambda: calls == ["eject", "load"] and bridge.canScan)
    _press(root, "scan")
    _wait(bridge, lambda: bridge.trays.rowCount() == 2 and not bridge.scanPending)


@pytest.mark.parametrize("name", ["scan", "eject", "load", "finish", "export"])
def test_a_refused_key_shows_a_banner_and_runs_nothing(tray, monkeypatch, name):
    bridge, root, _calls, _warnings = tray
    log = _record(bridge, monkeypatch)
    _force(
        bridge,
        can_scan=False,
        can_eject=False,
        can_load_next=False,
        can_finish_box=False,
        readonly=False,
    )
    bridge._trays.set_rows([])
    _press(root, name)
    _wait(bridge, lambda: bridge._controller._banner is not None, label="refusal banner")
    assert log == []
    assert bridge._controller._banner.message


def test_a_held_or_readonly_key_shows_no_banner(tray, monkeypatch):
    bridge, root, _calls, _warnings = tray
    _record(bridge, monkeypatch)
    _force(bridge, can_scan=False, readonly=False)
    _press(root, "scan", repeat=True)
    assert bridge._controller._banner is None
    _force(bridge, can_scan=False, readonly=True)
    _press(root, "scan")
    assert bridge._controller._banner is None


def _refuse_all(bridge):
    _force(
        bridge,
        can_scan=False,
        can_eject=False,
        can_load_next=False,
        can_finish_box=False,
        readonly=False,
    )


@pytest.mark.parametrize(
    "setup",
    [
        lambda c: c._scanner_problem("The scanner reported a problem", "detail"),
        lambda c: c._set(
            _banner=Banner("The Eject button watcher was left stopped: run systemctl")
        ),
        lambda c: c._set(_banner=Banner("This batch is open in another window", "locked")),
    ],
    ids=["scanner-problem", "eject-watcher", "locked-batch"],
)
def test_a_key_refusal_never_replaces_an_important_banner(tray, setup):
    bridge, root, _calls, _warnings = tray
    controller = bridge._controller
    _refuse_all(bridge)
    setup(controller)
    before = controller._banner
    _press(root, "scan")
    assert controller._banner is before
    assert not before.key_refusal


def test_a_key_refusal_replaces_a_key_refusal_and_cannot_hide_a_later_message(tray):
    bridge, root, _calls, _warnings = tray
    controller = bridge._controller
    _refuse_all(bridge)
    _press(root, "scan")
    first = controller._banner
    assert first.key_refusal
    _press(root, "eject")
    assert controller._banner.key_refusal and controller._banner is not first
    controller._set(_banner=Banner("Export failed"))
    bridge._key_banner_timer.timeout.emit()
    assert controller._banner.message == "Export failed"


def test_a_key_refusal_clears_on_a_timeout_and_on_the_next_good_key(tray, monkeypatch):
    bridge, root, _calls, _warnings = tray
    controller = bridge._controller
    log = _record(bridge, monkeypatch)
    _refuse_all(bridge)
    _press(root, "scan")
    assert controller._banner.key_refusal and bridge._key_banner_timer.isActive()
    bridge._key_banner_timer.timeout.emit()  # the few seconds are up
    assert controller._banner is None
    _press(root, "scan")
    assert controller._banner.key_refusal
    _force(bridge, can_eject=True)
    _press(root, "eject")
    assert log == ["eject"] and controller._banner is None


def test_cancel_has_focus_the_moment_the_finish_dialog_opens(tray):
    bridge, root, _calls, _warnings = tray
    _force(bridge, can_finish_box=True, readonly=False)
    finish = _dialog(root, "finishBoxDialog")
    finish.open()
    cancel = finish.findChild(QQuickItem, "confirmCancel")
    assert cancel.property("activeFocus")  # no event-loop turn in between


def test_a_second_scan_while_one_is_queued_is_refused(tray, monkeypatch):
    bridge, _root, _calls, _warnings = tray
    controller = bridge._controller
    submitted: list[str] = []
    monkeypatch.setattr(controller, "_reserved_scan", lambda job, msg: submitted.append(msg))
    controller.scan()
    assert len(submitted) == 1
    controller._scans_queued = 1
    controller.scan()
    assert len(submitted) == 1
    assert "Wait" in controller._banner.message
    controller._scans_queued = 0
