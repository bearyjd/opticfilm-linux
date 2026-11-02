"""Headless cleanup, export and holder interaction smoke tests."""

import pytest

pytestmark = pytest.mark.qt
pytest.importorskip("PySide6")

import test_qt_smoke as smoke  # noqa: E402
from PySide6.QtCore import QMetaObject, QObject, QUrl  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from test_qt_smoke import (  # noqa: E402
    TIMEOUT,
    _click,
    _drain_deferred_deletes,
    _prop,
    _scanned,
    _wait,
)

from opticfilm_linux.app.controller import Controller  # noqa: E402
from opticfilm_linux.app.prefs import MemoryPrefs  # noqa: E402
from opticfilm_linux.app.qt.app import create_engine  # noqa: E402
from opticfilm_linux.app.qt.bridge import Bridge  # noqa: E402
from opticfilm_linux.app.qt.images import PreviewProvider  # noqa: E402

qapp = smoke.qapp
loaded = smoke.loaded


def test_automatic_first_tray_can_be_checked_and_freed(loaded, tmp_path):
    bridge, engine, warnings = loaded
    bridge.newBatch(
        QUrl.fromLocalFile(str(tmp_path)).toString(),
        "Hands free",
        "",
        "",
        600,
        export_dir=str(tmp_path / "photos"),
        auto_export=True,
    )
    _wait(bridge, lambda: bridge.hasBatch and bridge.canScan)
    bridge.scan()
    _wait(bridge, lambda: bridge.heldTray == 1)
    root = engine.rootObjects()[0]
    _drain_deferred_deletes()
    button = root.findChild(QObject, "freeHeldRaw")
    assert button.property("visible")
    assert "flagged" in bridge.autoStatus.lower()
    assert len(list((tmp_path / "photos").glob("*.jpg"))) == 4
    for position in range(1, 5):
        bridge.setAccepted(1, position, True)
    _wait(
        bridge,
        lambda: all(
            s.owner_decided for s in bridge._controller._session.store.snapshot().trays[0].slides
        ),
    )
    QMetaObject.invokeMethod(button, "click")
    dialog = root.findChild(QObject, "heldReleaseDialog")
    _wait(bridge, lambda: dialog.property("visible"))
    assert "tray 1" in dialog.property("title")
    assert int(dialog.property("rawBytes")) > 0
    text = dialog.property("subtitle")
    assert dialog.property("rawBytes") in text
    assert "Later trays will be freed automatically" in text
    assert "opened the delivered JPEGs" in text
    assert not bridge._controller._session.store.snapshot().trays[0].raw_removed
    QMetaObject.invokeMethod(dialog, "reject")
    assert not bridge._controller._session.store.snapshot().first_tray_released
    QMetaObject.invokeMethod(button, "click")
    _wait(bridge, lambda: dialog.property("visible"))
    QMetaObject.invokeMethod(dialog, "accept")
    raw_role = next(
        role for role, name in bridge.trays.roleNames().items() if bytes(name) == b"rawRemoved"
    )
    _wait(bridge, lambda: bridge.trays.data(bridge.trays.index(0), raw_role))
    assert bridge.heldTray == 0
    assert not warnings


@pytest.mark.parametrize(
    "outcome",
    [
        "raw data kept",
        "raw data partly removed",
        "raw data removed; could not save cleanup completion",
    ],
)
def test_failed_automatic_cleanup_renders_actual_raw_outcome(loaded, tmp_path, outcome):
    bridge, engine, warnings = loaded
    _scanned(bridge, tmp_path, "Cleanup failure")
    store = bridge._controller._session.store
    store.update_tray(1, auto="failed", auto_failed=(1,), auto_error=f"{outcome}. Disk fault")
    bridge._controller._rebuild()
    _drain_deferred_deletes()
    root = engine.rootObjects()[0]
    queue = root.findChild(QQuickItem, "trayQueue")
    queue.setProperty("currentIndex", 0)
    # Keep the parentless delegate/content wrappers alive while reading their labels.
    retained_items = []

    def row_label(name):
        row = queue.property("currentItem")
        content = row.property("contentItem") if row is not None else None
        label = content.findChild(QQuickItem, name) if content is not None else None
        retained_items.extend((row, content, label))
        return label

    def outcome_is_rendered():
        label = row_label("trayAutoStatus1")
        return label is not None and label.isVisible() and outcome in label.property("text")

    _wait(bridge, outcome_is_rendered)
    status = row_label("trayAutoStatus1")
    slides = row_label("trayAutoSlides1")
    assert slides is not None and slides.isVisible()
    assert "Failed slides: 1" in slides.property("text")
    assert "Raw kept" not in status.property("text")
    assert "Raw kept" not in slides.property("text")
    assert not warnings


@pytest.mark.parametrize(
    "fmt,trailing_slash", [("jpeg", False), ("png8", False), ("tiff16", False), ("jpeg", True)]
)
def test_last_export_initializes_next_batch_without_changing_existing_batch(
    loaded, tmp_path, fmt, trailing_slash
):
    bridge, engine, warnings = loaded
    parent = QUrl.fromLocalFile(str(tmp_path)).toString()
    bridge.newBatch(parent, "Original", "", "", 600)
    _wait(bridge, lambda: bridge.hasBatch)
    original_session = bridge._state.batch_session
    bridge.newBatch(parent, "Exporting", "", "", 600)
    _wait(bridge, lambda: bridge.hasBatch and bridge._state.batch_session != original_session)
    folder_url = QUrl.fromLocalFile(str(tmp_path / "photos")).toString()
    bridge.startExport(folder_url + ("/" if trailing_slash else ""), fmt, "3600")
    _wait(bridge, lambda: bridge.exportFinished)
    assert bridge.lastExportFolder == folder_url
    bridge.closeBatch()
    _wait(bridge, lambda: not bridge.hasBatch)
    _drain_deferred_deletes()
    root = engine.rootObjects()[0]
    page = root.findChild(QObject, "startPage")
    assert page.property("exportFolder") == QUrl(folder_url)
    assert root.findChild(QObject, "newBatchExportFormat").property("currentValue") == fmt
    size = root.findChild(QObject, "newBatchJpegSize")
    assert size.property("currentValue") == "3600"
    assert size.property("enabled") == (fmt == "jpeg")
    page.setProperty("parentFolder", QUrl(parent))
    root.findChild(QObject, "newBatchName").setProperty("text", "Next")
    create = root.findChild(QObject, "createBatch")
    _wait(bridge, lambda: create.property("enabled"))
    assert QMetaObject.invokeMethod(create, "click")
    _wait(bridge, lambda: bridge.hasBatch)
    assert (bridge.batchExportFolder, bridge.batchExportFormat, bridge.batchJpegSize) == (
        folder_url,
        fmt,
        "3600",
    )
    bridge.openBatch(str(tmp_path / "Original"), False)
    _wait(bridge, lambda: bridge.batchName == "Original")
    assert (bridge.batchExportFolder, bridge.batchExportFormat, bridge.batchJpegSize) == (
        "",
        "jpeg",
        "full",
    )
    assert not warnings


def test_rejected_export_does_not_replace_remembered_defaults(loaded, tmp_path):
    from opticfilm_linux.app.prefs import export_defaults, remember_export

    bridge, _engine, warnings = loaded
    remember_export(bridge._prefs, "file:///previous", "png8", "3600")
    before = export_defaults(bridge._prefs)
    bridge.newBatch(QUrl.fromLocalFile(str(tmp_path)).toString(), "Reject", "", "", 600)
    _wait(bridge, lambda: bridge.hasBatch)
    bridge.startExport(
        QUrl.fromLocalFile(str(tmp_path / "Reject" / "raw")).toString(), "jpeg", "full"
    )
    _wait(bridge, lambda: bridge.hasBanner)
    assert export_defaults(bridge._prefs) == before
    assert not warnings


def test_section_controls_bind(loaded, tmp_path):
    from opticfilm_linux.batch.store import BatchStore

    bridge, engine, warnings = loaded
    root = engine.rootObjects()[0]
    page = root.findChild(QObject, "startPage")
    page.setProperty("parentFolder", QUrl.fromLocalFile(str(tmp_path)))
    root.findChild(QObject, "newBatchName").setProperty("text", "Section UI")
    collection = root.findChild(QObject, "newBatchCollection")
    assert collection is not None
    collection.setProperty("text", "family")
    root.findChild(QObject, "newBatchBox").setProperty("value", 2)
    root.findChild(QObject, "newBatchSection").setProperty("text", "01")
    root.findChild(QObject, "newBatchSectionSize").setProperty("value", 10)
    create = root.findChild(QObject, "createBatch")
    _wait(bridge, lambda: create.property("enabled"))
    assert QMetaObject.invokeMethod(create, "click")
    _wait(bridge, lambda: bridge.hasBatch or bridge.hasBanner)
    assert bridge.hasBatch, (bridge.bannerMessage, bridge.bannerDetail, warnings)
    _wait(bridge, lambda: bridge.canFinishBox)
    assert (bridge.collection, bridge.box, bridge.section, bridge.sectionSize) == (
        "family",
        2,
        "01",
        10,
    )
    _wait(bridge, lambda: root.findChild(QObject, "nextSectionButton") is not None)
    QMetaObject.invokeMethod(root.findChild(QObject, "nextSectionButton"), "click")
    _wait(bridge, lambda: bridge.section == "02")
    QMetaObject.invokeMethod(root.findChild(QObject, "finishBoxButton"), "click")
    _wait(bridge, lambda: bridge.box == 3)
    assert bridge.section == "01" and bridge.selectedTray == -1
    with BatchStore.open(tmp_path / "Section UI", readonly=True) as store:
        m = store.snapshot()
        assert (m.collection, m.box, m.section, m.section_size) == ("family", 3, "01", 10)
    assert not warnings


def test_unknown_cleanup_failure_is_terminal_and_visible(loaded):
    from opticfilm_linux.app.state import FreeSpaceView

    bridge, _, _ = loaded
    message = "Cleanup failed; raw cleanup outcome unknown (could not inspect raw files)"
    bridge._controller._set(_free_space=FreeSpaceView(((1, 10),), 10, message=message))
    _wait(bridge, lambda: bridge.freeSpaceText == message)
    assert bridge.freeSpaceText == message
    assert not bridge.freeSpaceReady


def test_first_load_card_reseats_then_reports_exit_code(qapp, sweep600_dir, tmp_path):
    from test_first_load import InteractiveScanner

    scanner = InteractiveScanner(sweep600_dir)
    c = Controller(lambda _dpi: scanner)
    bridge = Bridge(c, MemoryPrefs(), demo_dpi=600)
    provider = PreviewProvider(lambda n, p, k: c.preview_path(n, p, k))
    engine, warnings = create_engine(bridge, provider)
    try:
        root = engine.rootObjects()[0]
        bridge.newBatch(QUrl.fromLocalFile(str(tmp_path)).toString(), "First", "", "", 600)
        _wait(bridge, lambda: bridge.canFirstLoad)
        _click(root, "firstLoadButton")
        _wait(bridge, lambda: bridge.loadPhase == "await_reinsert")
        assert _prop(root, "firstLoadText", "text") == (
            "Pull the holder fully OUT, then push it back in to the stop. Press Done."
        )
        assert not _prop(root, "ejectButton", "enabled")
        _click(root, "firstLoadDoneButton")
        bridge.firstLoadDone()
        _wait(bridge, lambda: bridge.loadPhase == "done" and not bridge.holderBusy)
        assert bridge.loadExitCode == "0" and scanner.calls == ["load", "done"]
        assert _prop(root, "firstLoadExitCode", "text") == "Driver exit code: 0"
        assert warnings == []
    finally:
        bridge.shutdown()
        engine.deleteLater()


def test_first_load_card_cancel_warns_then_stops(qapp, sweep600_dir, tmp_path):
    from test_first_load import InteractiveScanner

    scanner = InteractiveScanner(sweep600_dir)
    c = Controller(lambda _dpi: scanner)
    bridge = Bridge(c, MemoryPrefs(), demo_dpi=600)
    engine, warnings = create_engine(bridge, PreviewProvider(lambda *_: None))
    try:
        root = engine.rootObjects()[0]
        bridge.newBatch(QUrl.fromLocalFile(str(tmp_path)).toString(), "Cancel", "", "", 600)
        _wait(bridge, lambda: bridge.canFirstLoad)
        _click(root, "firstLoadButton")
        _wait(bridge, lambda: bridge.loadPhase == "await_reinsert")
        _click(root, "firstLoadCancelButton")
        dialog = root.findChild(QObject, "firstLoadCancelDialog")
        _wait(bridge, lambda: dialog.property("visible"))
        assert "power cycle" in dialog.property("subtitle")
        QMetaObject.invokeMethod(dialog, "accepted")
        _wait(bridge, lambda: bridge.loadPhase == "failed" and not bridge.holderBusy)
        assert scanner.calls == ["load", "cancel"] and bridge.loadExitCode == "130"
        assert warnings == []
    finally:
        bridge.shutdown()
        engine.deleteLater()


def _held_first_load_scanner(sweep, phase):
    from opticfilm_linux.scanner.base import ScanError
    from test_first_load import InteractiveScanner

    class HeldScanner(InteractiveScanner):
        def load_interactive(self, on_prompt, done, cancel):
            self.calls.append("load")
            if phase != "jogging":
                on_prompt()
            if cancel.wait(TIMEOUT):
                self.calls.append("cancel")
            raise ScanError("cancelled, exit code 130")

    return HeldScanner(sweep)


@pytest.mark.parametrize("phase", ["jogging", "await_reinsert", "loading"])
def test_first_load_quit_warns_and_can_be_rejected(qapp, sweep600_dir, tmp_path, phase):
    scanner = _held_first_load_scanner(sweep600_dir, phase)
    c = Controller(lambda _dpi: scanner)
    bridge = Bridge(c, MemoryPrefs(), demo_dpi=600)
    engine, warnings = create_engine(bridge, PreviewProvider(lambda *_: None))
    stopped = False
    try:
        root = engine.rootObjects()[0]
        bridge.newBatch(QUrl.fromLocalFile(str(tmp_path)).toString(), "Quit", "", "", 600)
        _wait(bridge, lambda: bridge.canFirstLoad)
        _click(root, "firstLoadButton")
        target = "await_reinsert" if phase == "loading" else phase
        _wait(bridge, lambda: bridge.loadPhase == target and scanner.calls == ["load"])
        if phase == "loading":
            bridge.firstLoadDone()
            _wait(bridge, lambda: bridge.loadPhase == "loading")
        assert bridge.closeRequested() == "first_load_running"
        assert QMetaObject.invokeMethod(root, "close")
        dialog = root.findChild(QObject, "firstLoadQuitDialog")
        _wait(bridge, lambda: dialog.property("visible"))
        assert "power cycle" in dialog.property("subtitle")
        assert QMetaObject.invokeMethod(dialog, "reject")
        _wait(bridge, lambda: not dialog.property("visible"))
        assert root.property("visible") and bridge.loadPhase == phase
        assert scanner.calls == ["load"] and bridge.holderBusy
        assert QMetaObject.invokeMethod(root, "close")
        _wait(bridge, lambda: dialog.property("visible"))
        assert QMetaObject.invokeMethod(dialog, "accept")
        _wait(bridge, lambda: not root.property("visible"))
        bridge.shutdown()  # main() connects application quit to this same cleanup.
        stopped = True
        assert scanner.calls == ["load", "cancel"]
        assert warnings == []
    finally:
        if not stopped:
            bridge.shutdown()
        engine.deleteLater()


def test_empty_frames_confirmation_names_target_when_another_tray_is_selected(loaded, tmp_path):
    from dataclasses import replace

    from opticfilm_linux.batch.model import SlideStatus

    bridge, engine, warnings = loaded
    _scanned(bridge, tmp_path, "Empty confirmation")
    store = bridge._controller._session.store
    tray = store.snapshot().trays[0]
    store.update_tray(
        tray.number,
        slides=tuple(
            replace(s, status=SlideStatus.BLANK, owner_decided=False, blank_fingerprint="b" * 64)
            for s in tray.slides
        ),
    )
    selected = replace(
        tray,
        number=2,
        slides=tuple(
            replace(s, status=SlideStatus.ACCEPTED, owner_decided=True) for s in tray.slides
        ),
    )
    store.update(lambda m: replace(m, trays=(*m.trays, selected)))
    bridge.selectTray(2)
    bridge._controller._rebuild()
    _wait(
        bridge,
        lambda: bridge.emptyConfirmationCount == len(tray.slides) and bridge.selectedTray == 2,
    )
    root = engine.rootObjects()[0]
    _wait(bridge, lambda: _prop(root, "confirmEmptyFrames", "visible"))
    assert "frame(s) look empty" in _prop(root, "autoPipelineStatus", "text")
    assert "in tray 1" in _prop(root, "confirmEmptyFrames", "text")
    _click(root, "confirmEmptyFrames")
    _wait(bridge, lambda: _prop(root, "confirmReviewedEmptyFrames", "enabled"))
    assert bridge.emptyConfirmationCount == len(tray.slides)
    _click(root, "confirmReviewedEmptyFrames")
    _wait(bridge, lambda: bridge.emptyConfirmationCount == 0)
    assert all(
        s.status is SlideStatus.BLANK and s.empty_confirmed is not None
        for s in store.snapshot().trays[0].slides
    )
    assert store.snapshot().trays[1] == selected
