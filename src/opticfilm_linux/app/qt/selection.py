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
