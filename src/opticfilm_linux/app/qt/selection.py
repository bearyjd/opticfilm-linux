"""Contact-sheet selection properties and thin command hooks for QML."""

from PySide6.QtCore import QObject, Qt, Slot

from opticfilm_linux.batch.model import SlideStatus

FIELDS = {
    "brightness": "brightness_ev",
    "color": "color_strength",
    "grain": "grain_strength",
    "sharpen": "sharpen_amount",
}


class SelectionBridge(QObject):
    """Mixin base of Bridge: needs its _controller and _flush_now()."""

    @Slot(int, int, int)
    def selectSlide(self, number: int, position: int, modifiers: int) -> None:  # noqa: N802
        self._flush_now()
        self._controller.select_slide(
            number,
            position,
            bool(modifiers & Qt.KeyboardModifier.ControlModifier.value),
            bool(modifiers & Qt.KeyboardModifier.ShiftModifier.value),
        )

    @Slot()
    def selectAll(self) -> None:  # noqa: N802
        self._flush_now()
        self._controller.select_all()

    @Slot()
    def clearSelection(self) -> None:  # noqa: N802
        self._flush_now()
        self._controller.clear_selection()

    @Slot(int, bool)
    def selectTraySlides(self, number: int, additive: bool) -> None:  # noqa: N802
        self._flush_now()
        self._controller.select_tray_slides(number, additive)

    def _select_tray_slides(self, number: int, modifiers: int) -> None:
        self._controller.select_tray_slides(
            number,
            bool(modifiers & Qt.KeyboardModifier.ControlModifier.value),
            bool(modifiers & Qt.KeyboardModifier.ShiftModifier.value),
        )

    @Slot()
    def selectFlagged(self) -> None:  # noqa: N802
        self._flush_now()
        self._controller.select_where("flagged")

    @Slot()
    def selectFailed(self) -> None:  # noqa: N802
        self._flush_now()
        self._controller.select_where("failed")

    @Slot(int, bool)
    def moveSelectionFocus(self, delta: int, extend: bool) -> None:  # noqa: N802
        self._flush_now()
        self._controller.move_focus(delta, extend)

    @Slot(bool)
    def setSelectionAccepted(self, accepted: bool) -> None:  # noqa: N802
        self._flush_now()
        status = SlideStatus.ACCEPTED if accepted else SlideStatus.PENDING
        self._controller.set_selection_status(status)

    @Slot()
    def toggleSelectionAccepted(self) -> None:  # noqa: N802
        self._flush_now()
        self._controller.toggle_selection_accepted()

    @Slot(str)
    def setSelectionStatus(self, status: str) -> None:  # noqa: N802
        self._flush_now()
        if status == "rejected":
            status = SlideStatus.PENDING.value
        if status in (SlideStatus.ACCEPTED, SlideStatus.PENDING, SlideStatus.NEEDS_LOOK):
            self._controller.set_selection_status(SlideStatus(status))

    @Slot(str, float)
    def adjustSelection(self, field: str, value: float) -> None:  # noqa: N802
        self._flush_now()
        if field in FIELDS:
            self._controller.adjust_selection({FIELDS[field]: value})

    @Slot(int)
    def rotateSelection(self, quarter_turns: int) -> None:  # noqa: N802
        self._flush_now()
        self._controller.rotate_selection(quarter_turns)

    @Slot()
    def mirrorSelection(self) -> None:  # noqa: N802
        self._flush_now()
        self._controller.mirror_selection()

    @Slot()
    def resetSelection(self) -> None:  # noqa: N802
        self._flush_now()
        self._controller.reset_selection()

    @Slot()
    def reexportSelection(self) -> None:  # noqa: N802
        self._flush_now()
        self._controller.reexport_selection()

    @Slot()
    def undo(self) -> None:
        self._flush_now()
        self._controller.undo()

    # -- description and date (the metadata strip). Each slot returns "" when the edit was
    # started, or the reason it was refused (for an inline message). field: "description" or
    # "date" ("1978" or "1978-07"). Clear sets a blank at that level; Reset inherits again.
    # Batch scope: "from_now_on" (existing trays keep their values) or "inheriting".

    def _meta(self, edit, *args) -> str:
        self._flush_now()
        try:
            edit(*args)
        except ValueError as exc:
            return str(exc)
        return ""

    @Slot(str, str, result=str)
    def setSelectionMeta(self, field: str, value: str) -> str:  # noqa: N802
        return self._meta(self._controller.set_selection_meta, field, value)

    @Slot(str, result=str)
    def clearSelectionMeta(self, field: str) -> str:  # noqa: N802
        return self._meta(self._controller.set_selection_meta, field, "")

    @Slot(str, result=str)
    def resetSelectionMeta(self, field: str) -> str:  # noqa: N802
        return self._meta(self._controller.set_selection_meta, field, None)

    # The strip passes the slides it was editing ([[tray, position], ...]) so a click that
    # changes the selection before the edit is committed cannot redirect it.
    @Slot("QVariantList", str, str, result=str)
    def setSlidesMeta(self, keys: list, field: str, value: str) -> str:  # noqa: N802
        return self._meta(self._controller.set_slides_meta, keys, field, value)

    @Slot("QVariantList", str, result=str)
    def clearSlidesMeta(self, keys: list, field: str) -> str:  # noqa: N802
        return self._meta(self._controller.set_slides_meta, keys, field, "")

    @Slot("QVariantList", str, result=str)
    def resetSlidesMeta(self, keys: list, field: str) -> str:  # noqa: N802
        return self._meta(self._controller.set_slides_meta, keys, field, None)

    @Slot(int, str, str, result=str)
    def setTrayMeta(self, number: int, field: str, value: str) -> str:  # noqa: N802
        return self._meta(self._controller.set_tray_meta, number, field, value)

    @Slot(int, str, result=str)
    def clearTrayMeta(self, number: int, field: str) -> str:  # noqa: N802
        return self._meta(self._controller.set_tray_meta, number, field, "")

    @Slot(int, str, result=str)
    def resetTrayMeta(self, number: int, field: str) -> str:  # noqa: N802
        return self._meta(self._controller.set_tray_meta, number, field, None)

    @Slot(str, str, str, result=str)
    def setBatchMeta(self, field: str, value: str, scope: str) -> str:  # noqa: N802
        return self._meta(self._controller.set_batch_meta, field, value, scope)

    @Slot(str, str, result=str)
    def clearBatchMeta(self, field: str, scope: str) -> str:  # noqa: N802
        # the batch has no level above it, so a clear is also its reset
        return self._meta(self._controller.set_batch_meta, field, "", scope)
