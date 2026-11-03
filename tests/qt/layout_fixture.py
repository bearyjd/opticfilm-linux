"""Four real fake-scanner trays and immutable layout stress snapshots."""

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace

from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtQml import QQmlExpression
from PySide6.QtTest import QTest

from opticfilm_linux.app.controller import Controller
from opticfilm_linux.app.prefs import MemoryPrefs
from opticfilm_linux.app.qt.app import create_engine, scanner_factory
from opticfilm_linux.app.qt.bridge import Bridge
from opticfilm_linux.app.qt.images import PreviewProvider
from opticfilm_linux.app.state import Banner, FreeSpaceView
from opticfilm_linux.batch.model import TrayStage

DIALOG_NAMES = {
    "export": "exportDialog",
    "confirm-cleanup": "heldReleaseDialog",
    "load-help": "loadHelpDialog",
    "empty-frames": "emptyFramesDialog",
    "orientation": "orientationDialog",
    "first-load-quit": "firstLoadQuitDialog",
}


def pump(milliseconds=50):
    QCoreApplication.processEvents()
    QTest.qWait(milliseconds)
    QCoreApplication.processEvents()


def wait_for(condition, bridge=None, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pump(10)
        if bridge is not None:
            bridge.poll()
        if condition():
            return
    raise RuntimeError("layout fixture timed out")


def scan_trays(bridge, controller, directory):
    bridge.newBatch(QUrl.fromLocalFile(str(directory)).toString(), "Layout", "", "", 600)
    wait_for(lambda: bridge.hasBatch, bridge)
    store = controller._session.store
    for number in range(1, 5):
        wait_for(lambda: bridge.canScan, bridge)
        bridge.scan()
        wait_for(
            lambda n=number: len(store.snapshot().trays) == n and not bridge.scanPending, bridge
        )
        wait_for(lambda: all(t.stage == TrayStage.REVIEW for t in store.snapshot().trays), bridge)
        wait_for(
            lambda n=number: all(controller.preview_path(n, p, "after") for p in range(1, 5)),
            bridge,
        )
    bridge.selectAll()
    bridge.poll()
    assert len(bridge._state.selection) == 16
    bridge._timer.stop()
    return bridge._state


def worst_stack(main):
    return replace(
        main,
        load_phase="failed",
        load_exit_code=1,
        can_first_load=True,
        holder_latch_warning=True,
        can_confirm_holder_latched=True,
        holder_latch_text="Holder latch is unknown. Reseat and latch the holder before scanning.",
        auto_export=True,
        auto_status="Raw cleanup held: approve settings before releasing the raw scans.",
        held_tray=1,
        banner=Banner(
            "Synthetic scanner error: the previous operation did not finish.",
            "Evidence-only error details; no scanner connected.",
        ),
    )


@dataclass(frozen=True)
class LayoutFixture:
    app: object
    bridge: object
    engine: object
    warnings: list
    main: object
    startup_warnings: tuple
    dialogs: tuple

    @property
    def root(self):
        return self.engine.rootObjects()[0]

    def apply(self, state="contact-sheet", size=None, theme="dark"):
        self.close_dialogs()
        self.bridge._apply(worst_stack(self.main) if state == "worst-stack" else self.main)
        self.bridge.setTheme(theme)
        if size:
            self.root.resize(*size)
        pump(100)
        return self

    def close_dialogs(self):
        for _name, item in self.dialogs:
            if item.property("opened") is True:
                item.invoke("close")
        pump(30)

    def dialog(self, name):
        dialog = dict(self.dialogs)[name]
        return dialog

    def open_dialog(self, name):
        self.apply()
        if name == "free-space":
            view = FreeSpaceView(tuple((n, 1234567890) for n in range(1, 5)), 4938271560, total=4)
            self.bridge._apply(replace(self.main, free_space=view))
            pump()
        dialog = self.dialog(name)
        if name == "confirm-cleanup":
            dialog.setProperty("tray", 1)
            dialog.setProperty("rawBytes", "9876543210")
        if name == "empty-frames":
            dialog.setProperty("tray", 1)
            dialog.setProperty("sweepVersion", 1)
            dialog.setProperty(
                "previews",
                [
                    dict(
                        position=p, url=f"image://preview/1/{p}/before/1", fingerprint=f"layout-{p}"
                    )
                    for p in range(1, 5)
                ],
            )
        dialog.invoke("open")
        wait_for(lambda: dialog.property("opened") is True)
        if name == "empty-frames":
            wait_for(lambda: dialog.property("loaded") == 4)
        pump(100)
        return dialog


_FIND_DIALOG = """
(function() {
    var pending = [contentItem], seen = [];
    if (typeof data !== "undefined")
        for (var d = 0; d < data.length; d++) pending.push(data[d]);
    while (pending.length) {
        var item = pending.pop();
        if (!item || seen.indexOf(item) >= 0) continue;
        seen.push(item);
        if (item.objectName === NAME ||
            (NAME === "free-space" && String(item).indexOf("FreeSpaceDialog") === 0))
            return item;
        var lists = [item.children, item.data, item.contentData];
        for (var l = 0; l < lists.length; l++)
            if (lists[l]) for (var i = 0; i < lists[l].length; i++) pending.push(lists[l][i]);
        if (item.contentItem) pending.push(item.contentItem);
    }
    throw new Error("Missing layout popup " + NAME);
})()
"""


@dataclass(frozen=True)
class LayoutDialog:
    """Operate in QML so PySide never wraps internal popup objects."""

    engine: object
    root: object
    name: str

    def evaluate(self, expression):
        lookup = _FIND_DIALOG.replace("NAME", json.dumps(self.name))
        script = (
            "(function(target) {"
            "var contentItem = target.contentItem, footer = target.footer;"
            "return (" + expression + ");})(" + lookup + ")"
        )
        expr = QQmlExpression(self.engine.rootContext(), self.root, script)
        value, _undefined = expr.evaluate()
        if expr.hasError():
            raise RuntimeError(expr.error().toString())
        return value

    def property(self, name):
        return self.evaluate("target[" + json.dumps(name) + "]")

    def setProperty(self, name, value):
        self.evaluate("target[" + json.dumps(name) + "] = " + json.dumps(value))

    def invoke(self, method):
        self.evaluate("target[" + json.dumps(method) + "]()")

    def objectName(self):
        return self.name

    def rect(self):
        return json.loads(
            self.evaluate("""JSON.stringify((function() {
            var p = target.parent.mapToItem(null, target.x, target.y);
            return [p.x, p.y, target.width, target.height];
        })())""")
        )


def _layout_dialogs(engine, root):
    names = tuple(DIALOG_NAMES.items()) + (("free-space", "free-space"),)
    return tuple((name, LayoutDialog(engine, root, object_name)) for name, object_name in names)


@contextmanager
def create_layout(app, sweep_directory, directory, size=(1220, 780), theme="dark"):
    """Create a self-contained fixture; never selects a physical scanner."""
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QCoreApplication.processEvents()
    controller = Controller(scanner_factory(sweep_directory))
    bridge = Bridge(controller, MemoryPrefs(), demo_dpi=600)
    provider = PreviewProvider(lambda n, p, k: controller.preview_path(n, p, k))
    engine = None
    try:
        engine, warnings = create_engine(bridge, provider, size=size)
        assert engine.rootObjects(), warnings
        main = scan_trays(bridge, controller, directory)
        pump(100)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        fixture = LayoutFixture(
            app,
            bridge,
            engine,
            warnings,
            main,
            tuple(warnings),
            _layout_dialogs(engine, engine.rootObjects()[0]),
        )
        yield fixture.apply(size=size, theme=theme)
    finally:
        bridge.shutdown()
        if engine is not None:
            for root in engine.rootObjects():
                root.hide()
            engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()
