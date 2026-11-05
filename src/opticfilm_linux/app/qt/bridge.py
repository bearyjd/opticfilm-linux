"""The thin Qt object QML talks to: it mirrors ``AppState`` and forwards slots to the controller.

No behaviour lives here beyond translating types, debouncing slider moves, and remembering
recent batches. Everything it calls on the controller returns at once.
"""

from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication

from opticfilm_linux.app.controller import LOCKED_PREFIX, Controller
from opticfilm_linux.app.messages import bytes_text, load_command
from opticfilm_linux.app.prefs import (
    DEFAULT_DPI,
    DPI_CHOICES,
    THEMES,
    Prefs,
    export_defaults,
    forget_batch,
    read_theme,
    recent_batches,
    remember_batch,
    remember_export,
    remember_theme,
)
from opticfilm_linux.app.qt.destination import DestinationCheck
from opticfilm_linux.app.qt.models import DictListModel, SheetModel
from opticfilm_linux.app.qt.selection import FIELDS
from opticfilm_linux.app.qt.sheet import (
    _slide_dict,
    _tray_dict,
    filter_count,
    focused_slide,
    selection_summary,
    sheet_rows,
)
from opticfilm_linux.app.qt.start_bridge import StartBridge
from opticfilm_linux.app.qt.theme import make_palette, qml_palette_colors
from opticfilm_linux.app.state import EMPTY_STATE, AppState
from opticfilm_linux.batch.model import ROTATIONS, SlideStatus

TICK_MS = 500
DEBOUNCE_MS = 300
TRAY_KEYS = (
    "number",
    "stage",
    "stageText",
    "progress",
    "elapsed",
    "error",
    "needsLook",
    "rawRemoved",
    "canRecoverRaw",
    "auto",
    "autoFailed",
    "autoError",
    "autoFlagged",
)
SLIDE_KEYS = (
    "position",
    "status",
    "statusText",
    "flags",
    "rotation",
    "mirror",
    "brightness",
    "color",
    "grain",
    "sharpen",
    "ownerDecided",
    "exported",
    "updating",
    "afterUrl",
    "beforeUrl",
)


def _p(kind: Any, changed: Signal, read) -> Property:
    return Property(kind, lambda self: read(self._state, self), notify=changed)


class Bridge(StartBridge):
    changed = Signal()
    themeChanged = Signal()
    batchReplaced = Signal()  # noqa: N815
    automaticExportProblemChanged = Signal()  # noqa: N815
    heldReleaseReady = Signal(int, str)  # noqa: N815
    _heldReleasePrepared = Signal(int, int, str)  # noqa: N815

    selection = Property(
        "QVariantList",
        lambda self: [list(key) for key in sorted(self._state.selection)],
        notify=changed,
    )
    selectionFocus = Property(  # noqa: N815
        "QVariantList", lambda self: list(self._state.focus or ()), notify=changed
    )
    selectionSummary = Property(  # noqa: N815
        "QVariantMap", lambda self: selection_summary(self._state), notify=changed
    )
    focusedSlide = Property(  # noqa: N815
        "QVariantMap", lambda self: focused_slide(self._state), notify=changed
    )
    sheet = Property(QObject, lambda self: self._sheet, constant=True)
    undoLabel = Property(str, lambda self: self._state.undo_label, notify=changed)  # noqa: N815
    flaggedCount = Property(  # noqa: N815
        int, lambda self: filter_count(self._state, "flagged"), notify=changed
    )
    failedCount = Property(  # noqa: N815
        int, lambda self: filter_count(self._state, "failed"), notify=changed
    )

    def __init__(
        self,
        controller: Controller,
        prefs: Prefs,
        demo_dpi: int | None = None,
        parent=None,
        load_command_text: str | None = None,
        load_runner_note: str = "",
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._load_command = load_command_text or load_command(None)
        self._load_runner_note = load_runner_note
        self._prefs = prefs
        self._theme = read_theme(prefs)
        self._update_palette()
        self._demo_dpi = demo_dpi or 0
        self._state: AppState = EMPTY_STATE
        self._trays = DictListModel(TRAY_KEYS, self)
        self._slides = DictListModel(SLIDE_KEYS, self)
        self._sheet = SheetModel(TRAY_KEYS + ("slides",), self)
        self._pending: dict[tuple[int, int], dict[str, float]] = {}
        self._opening: Path | None = None
        self._last_attempt: Path | None = None
        self._pending_export: tuple[int, str, str, str] | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self.poll)
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(DEBOUNCE_MS)
        self._flush_timer.timeout.connect(self._flush)
        self._destination = DestinationCheck(self)
        self._destination.changed.connect(self.automaticExportProblemChanged)
        self._heldReleasePrepared.connect(self._held_release_ready)

    def start(self) -> None:
        self._timer.start()

    def _update_palette(self) -> None:
        self._palette = make_palette(self._theme)
        app = QGuiApplication.instance()
        if app is not None:
            app.setPalette(self._palette)

    theme = Property(str, lambda self: self._theme, notify=themeChanged)
    paletteColors = Property(  # noqa: N815
        "QVariantMap", lambda self: qml_palette_colors(self._palette), notify=themeChanged
    )

    @Slot(str)
    def setTheme(self, name: str) -> None:
        if name not in THEMES or name == self._theme:
            return
        remember_theme(self._prefs, name)
        self._theme = name
        self._update_palette()
        self.themeChanged.emit()

    # -- state mirror --------------------------------------------------------------------

    @Slot()
    def poll(self) -> None:
        self._flush_now()  # tick may schedule automatic export and cleanup
        if self._controller.tick():
            self._apply(self._controller.state())

    def _apply(self, state: AppState) -> None:
        replaced = state.batch_session != self._state.batch_session
        pending = self._pending_export
        if pending is not None:
            session, folder, fmt, size = pending
            if session != state.batch_session:
                self._pending_export = None
            elif (
                state.export is not None
                and state.export is not self._state.export
                and (state.export_dir, state.export_format, state.jpeg_size) == (folder, fmt, size)
            ):
                # The controller has validated and saved this request before starting it.
                remember_export(
                    self._prefs,
                    QUrl.fromLocalFile(folder).toString(),
                    state.export_format,
                    state.jpeg_size,
                )
                self._pending_export = None
        self._state = state
        self._trays.set_rows([_tray_dict(t) for t in state.trays])
        self._sheet.set_rows(sheet_rows(state))
        number = state.selected
        self._slides.set_rows(
            [] if number is None else [_slide_dict(number, c) for c in state.slides]
        )
        if state.batch_name is not None and self._opening is not None:
            remember_batch(self._prefs, self._opening)
            self._opening = None
        self.changed.emit()
        if replaced:
            self.batchReplaced.emit()

    trays = Property(QObject, lambda self: self._trays, constant=True)
    slides = Property(QObject, lambda self: self._slides, constant=True)
    demoDpi = Property(int, lambda self: self._demo_dpi, constant=True)  # noqa: N815
    defaultDpi = Property(int, lambda self: self._demo_dpi or DEFAULT_DPI, constant=True)  # noqa: N815
    dpiChoices = Property("QVariantList", lambda self: list(DPI_CHOICES), constant=True)  # noqa: N815
    rotationChoices = Property("QVariantList", lambda self: list(ROTATIONS), constant=True)  # noqa: N815
    loadCommand = Property(str, lambda self: self._load_command, constant=True)  # noqa: N815
    loadRunnerNote = Property(str, lambda self: self._load_runner_note, constant=True)  # noqa: N815
    nextStripEnabled = Property(  # noqa: N815
        bool, lambda self: self._controller.next_strip_enabled, constant=True
    )
    skipEmptyEnabled = Property(  # noqa: N815
        bool, lambda self: self._controller.skip_empty_enabled, constant=True
    )
    skipGapsEnabled = Property(  # noqa: N815
        bool, lambda self: self._controller.skip_gaps_enabled, constant=True
    )
    scanOpenings = _p("QVariantList", changed, lambda s, _: list(s.scan_openings))  # noqa: N815

    hasBatch = _p(bool, changed, lambda s, _: s.batch_name is not None)  # noqa: N815
    batchName = _p(str, changed, lambda s, _: s.batch_name or "")  # noqa: N815
    readonly = _p(bool, changed, lambda s, _: s.readonly)
    batchRotation = _p(int, changed, lambda s, _: s.default_rotation)  # noqa: N815
    batchMirror = _p(bool, changed, lambda s, _: s.default_mirror)  # noqa: N815
    batchExportFolder = _p(  # noqa: N815
        str,
        changed,
        lambda s, _: QUrl.fromLocalFile(s.export_dir).toString() if s.export_dir else "",
    )
    batchExportFormat = _p(str, changed, lambda s, _: s.export_format)  # noqa: N815
    batchJpegSize = _p(str, changed, lambda s, _: s.jpeg_size)  # noqa: N815
    batchAutoExport = _p(bool, changed, lambda s, _: s.auto_export)  # noqa: N815
    autoStatus = _p(str, changed, lambda s, _: s.auto_status)  # noqa: N815
    emptyConfirmationTray = _p(int, changed, lambda s, _: s.empty_confirmation_tray or 0)
    emptyConfirmationCount = _p(int, changed, lambda s, _: s.empty_confirmation_count)
    emptyConfirmationSweepVersion = _p(
        int, changed, lambda s, _: s.empty_confirmation_sweep_version
    )
    emptyConfirmationPreviews = _p(
        "QVariantList",
        changed,
        lambda s, _: [
            {
                "position": p,
                "fingerprint": fingerprint,
                "url": f"image://preview/{s.empty_confirmation_tray}/{p}/before/{token}"
                if token
                else "",
            }
            for p, fingerprint, token in s.empty_confirmation_previews
        ],
    )
    heldTray = _p(int, changed, lambda s, _: s.held_tray or 0)  # noqa: N815
    collection = _p(str, changed, lambda s, _: s.collection)
    box = _p(int, changed, lambda s, _: s.box)
    section = _p(str, changed, lambda s, _: s.section)
    sectionSize = _p(int, changed, lambda s, _: s.section_size)  # noqa: N815
    canFinishBox = _p(bool, changed, lambda s, _: s.can_finish_box)  # noqa: N815
    scannerText = _p(str, changed, lambda s, _: s.scanner_text)  # noqa: N815
    holderLatchText = _p(str, changed, lambda s, _: s.holder_latch_text)  # noqa: N815
    holderLatchWarning = _p(bool, changed, lambda s, _: s.holder_latch_warning)  # noqa: N815
    canConfirmHolderLatched = _p(bool, changed, lambda s, _: s.can_confirm_holder_latched)  # noqa: N815
    canScan = _p(bool, changed, lambda s, _: s.can_scan)  # noqa: N815
    canEject = _p(bool, changed, lambda s, _: s.can_eject)  # noqa: N815
    canFirstLoad = _p(bool, changed, lambda s, _: s.can_first_load)  # noqa: N815
    loadPhase = _p(str, changed, lambda s, _: s.load_phase)  # noqa: N815
    loadExitCode = _p(
        str, changed, lambda s, _: "" if s.load_exit_code is None else str(s.load_exit_code)
    )  # noqa: N815
    canLoadNext = _p(bool, changed, lambda s, _: s.can_load_next)  # noqa: N815
    holderBusy = _p(bool, changed, lambda s, _: s.holder_busy)  # noqa: N815
    canConfirmScanner = _p(bool, changed, lambda s, _: s.can_confirm_scanner)  # noqa: N815
    scannerStatus = _p(str, changed, lambda s, _: s.scanner.value)  # noqa: N815
    scanPending = _p(bool, changed, lambda s, _: s.scan_pending)  # noqa: N815
    selectedTray = _p(int, changed, lambda s, _: -1 if s.selected is None else s.selected)  # noqa: N815
    hasBanner = _p(bool, changed, lambda s, _: s.banner is not None)  # noqa: N815
    bannerMessage = _p(str, changed, lambda s, _: s.banner.message if s.banner else "")  # noqa: N815
    bannerDetail = _p(str, changed, lambda s, _: s.banner.detail if s.banner else "")  # noqa: N815
    bannerLocked = _p(  # noqa: N815
        bool, changed, lambda s, _: bool(s.banner and s.banner.detail.startswith(LOCKED_PREFIX))
    )
    hasExport = _p(bool, changed, lambda s, _: s.export is not None)  # noqa: N815
    exportRunning = _p(bool, changed, lambda s, _: bool(s.export and s.export.running))  # noqa: N815
    exportFinished = _p(bool, changed, lambda s, _: bool(s.export and s.export.finished))  # noqa: N815
    exportDone = _p(int, changed, lambda s, _: s.export.done if s.export else 0)  # noqa: N815
    exportTotal = _p(int, changed, lambda s, _: s.export.total if s.export else 0)  # noqa: N815
    exportWritten = _p(int, changed, lambda s, _: s.export.written if s.export else 0)  # noqa: N815
    exportCancelled = _p(bool, changed, lambda s, _: bool(s.export and s.export.cancelled))  # noqa: N815
    exportAlreadyExported = _p(  # noqa: N815
        int, changed, lambda s, _: s.export.already_exported if s.export else 0
    )
    exportNotAccepted = _p(  # noqa: N815
        int, changed, lambda s, _: s.export.not_accepted if s.export else 0
    )
    exportRawFreed = _p(int, changed, lambda s, _: s.export.raw_freed if s.export else 0)  # noqa: N815
    exportMessage = _p(str, changed, lambda s, _: s.export.message if s.export else "")  # noqa: N815
    freeSpaceReady = _p(  # noqa: N815
        bool,
        changed,
        lambda s, _: bool(
            s.free_space
            and s.free_space.trays
            and not s.free_space.running
            and s.free_space.freed is None
            and not s.free_space.message
        ),
    )
    hasFreeSpace = _p(bool, changed, lambda s, _: s.free_space is not None)  # noqa: N815
    freeSpaceRunning = _p(  # noqa: N815
        bool, changed, lambda s, _: bool(s.free_space and s.free_space.running)
    )
    freeSpaceDone = _p(  # noqa: N815
        bool, changed, lambda s, _: bool(s.free_space and s.free_space.freed is not None)
    )
    freeSpaceText = _p(str, changed, lambda s, _: _free_text(s))  # noqa: N815
    lastExportFolder = _p(  # noqa: N815
        str, changed, lambda _s, b: export_defaults(b._prefs).folder
    )
    lastExportFormat = _p(  # noqa: N815
        str, changed, lambda _s, b: export_defaults(b._prefs).format
    )
    lastExportJpegSize = _p(  # noqa: N815
        str, changed, lambda _s, b: export_defaults(b._prefs).jpeg_size
    )
    recentBatches = _p(  # noqa: N815
        "QVariantList",
        changed,
        lambda _s, _b: [
            {"path": str(r.path), "name": r.path.name, "exists": r.exists}
            for r in recent_batches(_b._prefs)
        ],
    )

    # -- batch ---------------------------------------------------------------------------

    automaticExportProblem = Property(  # noqa: N815
        str, lambda self: self._destination.problem, notify=automaticExportProblemChanged
    )

    @Slot(bool)
    def finishBox(self, next_box: bool) -> None:  # noqa: N802
        self._flush_now()
        self._controller.finish_box(next_box)

    @Slot(str, bool)
    def openBatch(self, path: str, readonly: bool) -> None:  # noqa: N802
        self._pending.clear()
        self._opening = self._last_attempt = Path(path)
        self._controller.open_batch(Path(path), readonly=readonly)

    @Slot()
    def openLastReadonly(self) -> None:  # noqa: N802
        if self._last_attempt is not None:
            self.openBatch(str(self._last_attempt), True)

    @Slot(str)
    def forgetBatch(self, path: str) -> None:  # noqa: N802
        forget_batch(self._prefs, Path(path))
        self.changed.emit()

    @Slot()
    def closeBatch(self) -> None:  # noqa: N802
        self._flush_now()
        self._controller.close_batch()

    # -- scanning and review -------------------------------------------------------------

    @Slot()
    def scan(self) -> None:
        self._flush_now()
        self._controller.scan()

    @Slot("QVariantList")
    def setScanOpenings(self, openings: list) -> None:  # noqa: N802
        # passed on as given: the controller checks the switch first, then refuses non-integers
        self._controller.set_scan_openings(tuple(openings))

    @Slot(int)
    def rescan(self, number: int) -> None:
        self._flush_now()
        self._controller.rescan(number)

    @Slot()
    def confirmHolderLatched(self) -> None:  # noqa: N802
        self._controller.confirm_holder_latched()

    @Slot()
    def ejectHolder(self) -> None:  # noqa: N802
        self._controller.eject_holder()

    @Slot()
    def firstLoad(self) -> None:  # noqa: N802
        self._controller.first_load()

    @Slot()
    def firstLoadDone(self) -> None:  # noqa: N802
        self._controller.first_load_done()

    @Slot()
    def firstLoadCancel(self) -> None:  # noqa: N802
        self._controller.first_load_cancel()

    @Slot()
    def loadNextHolder(self) -> None:  # noqa: N802
        self._controller.load_next_holder()

    @Slot()
    def confirmScannerReset(self) -> None:  # noqa: N802
        self._controller.confirm_scanner_reset()

    @Slot()
    def copyLoadCommand(self) -> None:  # noqa: N802
        QGuiApplication.clipboard().setText(self._load_command)

    @Slot(int)
    @Slot(int, int)
    def selectTray(self, number: int, modifiers: int | None = None) -> None:  # noqa: N802
        self._flush_now()
        if modifiers is None:
            self._controller.select_tray(number)
        else:
            self._select_tray_slides(number, modifiers)

    @Slot(int, int, bool)
    def setAccepted(self, number: int, position: int, accepted: bool) -> None:  # noqa: N802
        self._flush_now()
        status = SlideStatus.ACCEPTED if accepted else SlideStatus.NEEDS_LOOK
        self._controller.set_status(number, position, status)

    @Slot(int, int)
    def markEmpty(self, number: int, position: int) -> None:  # noqa: N802
        self._flush_now()
        self._controller.set_status(number, position, SlideStatus.BLANK)

    @Slot(int, int, int)
    def rotate(self, number: int, position: int, quarter_turns: int) -> None:
        self._flush_now()
        self._controller.rotate(number, position, quarter_turns)

    @Slot(int, int)
    def mirror(self, number: int, position: int) -> None:
        self._flush_now()
        self._controller.mirror(number, position)

    @Slot(int, bool)
    def setBatchOrientation(self, rotation: int, mirror: bool) -> None:  # noqa: N802
        self._flush_now()  # a slider moved a moment ago is stored first
        self._controller.set_batch_orientation(rotation, mirror)

    @Slot(int, int, str, float)
    def adjust(self, number: int, position: int, field: str, value: float) -> None:
        """Queue one slider value; the last value per slide is applied after a short pause."""
        if field in FIELDS:
            self._pending.setdefault((number, position), {})[field] = value
            self._flush_timer.start()

    @Slot(int, int)
    def resetSlide(self, number: int, position: int) -> None:  # noqa: N802
        self._pending.pop((number, position), None)
        self._flush_now()
        self._controller.reset_slide(number, position)

    @Slot()
    def acceptAll(self) -> None:  # noqa: N802
        self._flush_now()
        self._controller.accept_all()

    def _flush_now(self) -> None:
        self._flush_timer.stop()
        self._flush()

    def _flush(self) -> None:
        pending, self._pending = self._pending, {}
        for (number, position), fields in pending.items():
            changes = {FIELDS[name]: value for name, value in fields.items()}
            self._controller.adjust_slide(number, position, changes)

    # -- export and free disk space ------------------------------------------------------

    @Slot(str, str)
    @Slot(str, str, str)
    def startExport(self, folder_url: str, fmt: str, jpeg_size: str = "full") -> None:  # noqa: N802
        self._flush_now()  # the export must see the slider the owner just moved
        folder = QUrl(folder_url).toLocalFile() or folder_url
        directory = Path(folder)
        self._pending_export = (self._state.batch_session, str(directory), fmt, jpeg_size)
        self._controller.export(directory, fmt, jpeg_size)  # type: ignore[arg-type]

    @Slot()
    def reexportAllAccepted(self) -> None:  # noqa: N802
        self._flush_now()
        self._controller.reexport_all_accepted()

    @Slot()
    def cancelExport(self) -> None:  # noqa: N802
        self._controller.cancel_export()

    @Slot()
    def dismissExport(self) -> None:  # noqa: N802
        self._controller.dismiss_export()

    @Slot()
    def requestFreeSpace(self) -> None:  # noqa: N802
        self._flush_now()
        self._controller.request_free_space()

    @Slot()
    def confirmFreeSpace(self) -> None:  # noqa: N802
        self._flush_now()
        self._controller.confirm_free_space()

    @Slot(int, int, "QVariantList")
    def confirmEmptyFrames(self, number: int, sweep_version: int, previews: list) -> None:  # noqa: N802
        self._flush_now()
        positions = tuple(int(item["position"]) for item in previews)
        fingerprints = {int(item["position"]): str(item["fingerprint"]) for item in previews}
        self._controller.confirm_empty_frames(
            number,
            positions,
            expected_fingerprints=fingerprints,
            expected_sweep_version=sweep_version,
        )

    @Slot(int)
    def requestHeldRelease(self, number: int) -> None:  # noqa: N802
        self._flush_now()
        session = self._state.batch_session
        self._controller.request_held_release(
            number, lambda size: self._heldReleasePrepared.emit(session, number, str(size))
        )

    @Slot(int, int, str)
    def _held_release_ready(self, session: int, number: int, size: str) -> None:
        if session == self._state.batch_session and number == self._state.held_tray:
            self.heldReleaseReady.emit(number, size)

    @Slot(int)
    def confirmFreeHeld(self, number: int) -> None:  # noqa: N802
        self._flush_now()
        self._controller.confirm_free_held(number)

    @Slot(int)
    def recoverInterruptedCleanup(self, number: int) -> None:  # noqa: N802
        self._flush_now()
        self._controller.recover_interrupted_cleanup(number)

    @Slot()
    def cancelFreeSpace(self) -> None:  # noqa: N802
        self._controller.cancel_free_space()

    @Slot()
    def dismissFreeSpace(self) -> None:  # noqa: N802
        self._controller.dismiss_free_space()

    @Slot()
    def dismissBanner(self) -> None:  # noqa: N802
        self._controller.dismiss_banner()

    # -- closing -------------------------------------------------------------------------

    @Slot(result=str)
    def closeRequested(self) -> str:  # noqa: N802
        return self._controller.shutdown_decision()

    @Slot()
    def shutdown(self) -> None:
        self._timer.stop()
        self._destination.close()
        self._flush_now()
        self._controller.shutdown()

    @Slot(int, int, str, result=str)
    def previewPath(self, number: int, position: int, kind: str) -> str:  # noqa: N802
        path = self._controller.preview_path(number, position, kind)  # type: ignore[arg-type]
        return "" if path is None else str(path)


def _free_text(state: AppState) -> str:
    view = state.free_space
    if view is None:
        return ""
    if view.message and view.freed is None:
        return view.message
    if view.freed is not None:
        result = f"Freed {bytes_text(view.freed)}."
        return f"{view.message} {result}" if view.message else result
    names = ", ".join(f"tray {n} ({bytes_text(b)})" for n, b in view.trays)
    if not view.trays:
        return "There are no fully exported trays with raw scans to remove."
    return f"This removes the raw scans of {names}: {bytes_text(view.total_bytes)} in all."
