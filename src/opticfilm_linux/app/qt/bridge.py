"""The thin Qt object QML talks to: it mirrors ``AppState`` and forwards slots to the controller.

No behaviour lives here beyond translating types, debouncing slider moves, and remembering
recent batches. Everything it calls on the controller returns at once.
"""

from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication

from opticfilm_linux.app.controller import LOCKED_PREFIX, Controller
from opticfilm_linux.app.messages import bytes_text, load_command, slide_status_text
from opticfilm_linux.app.prefs import (
    DEFAULT_DPI,
    DPI_CHOICES,
    Prefs,
    batch_subfolder,
    export_defaults,
    forget_batch,
    recent_batches,
    remember_batch,
    remember_export,
)
from opticfilm_linux.app.qt.models import DictListModel
from opticfilm_linux.app.state import EMPTY_STATE, AppState, SlideCard, TrayRow
from opticfilm_linux.batch.model import ROTATIONS, BatchDefaults, SlideStatus
from opticfilm_linux.batch.slots import slot_orientations

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
FIELDS = {
    "brightness": "brightness_ev",
    "color": "color_strength",
    "grain": "grain_strength",
    "sharpen": "sharpen_amount",
}
DEFAULTS = BatchDefaults()


def _tray_dict(row: TrayRow) -> dict[str, Any]:
    elapsed = "" if row.elapsed_s is None else f"{int(row.elapsed_s)} s"
    return {
        "number": row.number,
        "stage": row.stage.value,
        "stageText": row.stage_text,
        "progress": -1.0 if row.progress is None else row.progress,
        "elapsed": elapsed,
        "error": row.error or "",
        "needsLook": row.needs_look,
        "rawRemoved": row.raw_removed,
    }


def _slide_dict(tray: int, card: SlideCard) -> dict[str, Any]:
    s = card.settings
    base = f"image://preview/{tray}/{card.position}"
    return {
        "position": card.position,
        "status": card.status.value,
        "statusText": slide_status_text(card.status),
        "flags": list(card.flags),
        "rotation": s.rotation,
        "mirror": s.mirror,
        "brightness": s.brightness_ev,
        "color": DEFAULTS.color_strength if s.color_strength is None else s.color_strength,
        "grain": DEFAULTS.grain_strength if s.grain_strength is None else s.grain_strength,
        "sharpen": DEFAULTS.sharpen_amount if s.sharpen_amount is None else s.sharpen_amount,
        "ownerDecided": card.owner_decided,
        "exported": card.exported,
        "updating": card.updating,
        "afterUrl": f"{base}/after/{card.preview_token}" if card.preview_token else "",
        "beforeUrl": f"{base}/before/{card.preview_token}" if card.preview_token else "",
    }


def _p(kind: Any, changed: Signal, read) -> Property:
    return Property(kind, lambda self: read(self._state, self), notify=changed)


class Bridge(QObject):
    changed = Signal()
    batchReplaced = Signal()  # noqa: N815

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
        self._demo_dpi = demo_dpi or 0
        self._state: AppState = EMPTY_STATE
        self._trays = DictListModel(TRAY_KEYS, self)
        self._slides = DictListModel(SLIDE_KEYS, self)
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

    def start(self) -> None:
        self._timer.start()

    # -- state mirror --------------------------------------------------------------------

    @Slot()
    def poll(self) -> None:
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
    scannerText = _p(str, changed, lambda s, _: s.scanner_text)  # noqa: N815
    holderLatchText = _p(str, changed, lambda s, _: s.holder_latch_text)  # noqa: N815
    holderLatchWarning = _p(bool, changed, lambda s, _: s.holder_latch_warning)  # noqa: N815
    canConfirmHolderLatched = _p(bool, changed, lambda s, _: s.can_confirm_holder_latched)  # noqa: N815
    canScan = _p(bool, changed, lambda s, _: s.can_scan)  # noqa: N815
    canEject = _p(bool, changed, lambda s, _: s.can_eject)  # noqa: N815
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

    @Slot(str, str, str, str, int)
    @Slot(str, str, str, str, int, int)
    @Slot(str, str, str, str, int, int, bool)
    @Slot(str, str, str, str, int, int, bool, "QVariantList")
    @Slot(str, str, str, str, int, int, bool, "QVariantList", "QVariantList")
    @Slot(str, str, str, str, int, int, bool, "QVariantList", "QVariantList", str)
    @Slot(str, str, str, str, int, int, bool, "QVariantList", "QVariantList", str, str)
    @Slot(str, str, str, str, int, int, bool, "QVariantList", "QVariantList", str, str, str)
    def newBatch(  # noqa: N802
        self,
        parent_url: str,
        name: str,
        album: str,
        date: str,
        dpi: int,
        rotation: int = 0,
        mirror: bool = False,
        slot_rotations: list[int] | None = None,
        slot_mirrors: list[bool] | None = None,
        export_dir: str = "",
        export_format: str = "jpeg",
        jpeg_size: str = "full",
    ) -> None:
        """``slot_rotations`` and ``slot_mirrors`` are per holder slot, slot 1 first."""
        rotations = {i: int(r) for i, r in enumerate(slot_rotations or (), start=1)}
        mirrored = [i for i, m in enumerate(slot_mirrors or (), start=1) if m]
        folder = batch_subfolder(Path(QUrl(parent_url).toLocalFile()), name)
        self._pending.clear()
        self._opening = self._last_attempt = folder
        self._controller.new_batch(
            folder,
            name.strip() or folder.name,
            album,
            date or None,
            dpi,
            rotation,
            mirror,
            slot_orientations(rotations, mirrored),
            export_dir=QUrl(export_dir).toLocalFile() or export_dir,
            export_format=export_format,  # type: ignore[arg-type]
            jpeg_size=jpeg_size,  # type: ignore[arg-type]
        )

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
        self._controller.scan()

    @Slot(int)
    def rescan(self, number: int) -> None:
        self._controller.rescan(number)

    @Slot()
    def confirmHolderLatched(self) -> None:  # noqa: N802
        self._controller.confirm_holder_latched()

    @Slot()
    def ejectHolder(self) -> None:  # noqa: N802
        self._controller.eject_holder()

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
    def selectTray(self, number: int) -> None:  # noqa: N802
        self._controller.select_tray(number)

    @Slot(int, int, bool)
    def setAccepted(self, number: int, position: int, accepted: bool) -> None:  # noqa: N802
        status = SlideStatus.ACCEPTED if accepted else SlideStatus.NEEDS_LOOK
        self._controller.set_status(number, position, status)

    @Slot(int, int, int)
    def rotate(self, number: int, position: int, quarter_turns: int) -> None:
        self._controller.rotate(number, position, quarter_turns)

    @Slot(int, int)
    def mirror(self, number: int, position: int) -> None:
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
        self._controller.reset_slide(number, position)

    @Slot()
    def acceptAll(self) -> None:  # noqa: N802
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
        self._controller.request_free_space()

    @Slot()
    def confirmFreeSpace(self) -> None:  # noqa: N802
        self._controller.confirm_free_space()

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
    if view.freed is not None:
        return f"Freed {bytes_text(view.freed)}."
    names = ", ".join(f"tray {n} ({bytes_text(b)})" for n, b in view.trays)
    if not view.trays:
        return "There are no fully exported trays with raw scans to remove."
    return f"This removes the raw scans of {names}: {bytes_text(view.total_bytes)} in all."
