"""Start-page slots of the bridge: creating a batch and checking what its form asks for."""

import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QUrl, Slot

from opticfilm_linux.app.prefs import batch_subfolder
from opticfilm_linux.app.qt.selection import SelectionBridge
from opticfilm_linux.batch.guess import guess_batch
from opticfilm_linux.batch.metadata import EstimatedDate
from opticfilm_linux.batch.slots import slot_orientations


class StartBridge(SelectionBridge):
    """Mixin base of Bridge: needs its _controller, _destination, _pending, _opening and
    _last_attempt."""

    @Slot(bool, str, str, str)
    def checkAutomaticExport(
        self, enabled: bool, parent_url: str, name: str, export_url: str
    ) -> None:  # noqa: N802
        """Debounce filesystem checks and invalidate any result for previous inputs."""
        destination = QUrl(export_url)
        parent = QUrl(parent_url)
        problem = ""
        if not enabled:
            problem = "Automatic export is off."
        elif not export_url.strip():
            problem = "Choose an export folder for automatic export."
        elif not destination.isLocalFile():
            problem = "Choose a local export folder for automatic export."
        elif not parent.isLocalFile() or not name.strip():
            problem = "Choose a batch folder and enter a batch name."
        self._destination.request(parent.toLocalFile(), name, destination.toLocalFile(), problem)

    @Slot(str, str, str, str, int)
    @Slot(str, str, str, str, int, int)
    @Slot(str, str, str, str, int, int, bool)
    @Slot(str, str, str, str, int, int, bool, "QVariantList")
    @Slot(str, str, str, str, int, int, bool, "QVariantList", "QVariantList")
    @Slot(str, str, str, str, int, int, bool, "QVariantList", "QVariantList", str)
    @Slot(str, str, str, str, int, int, bool, "QVariantList", "QVariantList", str, str)
    @Slot(str, str, str, str, int, int, bool, "QVariantList", "QVariantList", str, str, str)
    @Slot(str, str, str, str, int, int, bool, "QVariantList", "QVariantList", str, str, str, str)
    @Slot(
        str, str, str, str, int, int, bool, "QVariantList", "QVariantList", str, str, str, str, int
    )
    @Slot(
        str,
        str,
        str,
        str,
        int,
        int,
        bool,
        "QVariantList",
        "QVariantList",
        str,
        str,
        str,
        str,
        int,
        str,
    )
    @Slot(
        str,
        str,
        str,
        str,
        int,
        int,
        bool,
        "QVariantList",
        "QVariantList",
        str,
        str,
        str,
        str,
        int,
        str,
        int,
    )
    @Slot(
        str,
        str,
        str,
        str,
        int,
        int,
        bool,
        "QVariantList",
        "QVariantList",
        str,
        str,
        str,
        str,
        int,
        str,
        int,
        bool,
    )
    @Slot(
        str,
        str,
        str,
        str,
        int,
        int,
        bool,
        "QVariantList",
        "QVariantList",
        str,
        str,
        str,
        str,
        int,
        str,
        int,
        bool,
        bool,
    )
    @Slot(
        str,
        str,
        str,
        str,
        int,
        int,
        bool,
        "QVariantList",
        "QVariantList",
        str,
        str,
        str,
        str,
        int,
        str,
        int,
        bool,
        bool,
        str,
    )
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
        collection: str = "",
        box: int = 1,
        section: str = "a",
        section_size: int = 140,
        auto_export: bool = False,
        keep_raw: bool = False,
        description: str = "",
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
            auto_export=auto_export,
            keep_raw=keep_raw,
            collection=collection,
            box=box,
            section=section,
            section_size=section_size,
            description=description,
        )

    @Slot(str, result="QVariantMap")
    def guessBatch(self, text: str) -> dict[str, Any]:  # noqa: N802
        """A pure proposal for the start page from what the owner typed: no state changes.

        ``date`` is the default pick ("1974", "1974-07" or ""), ``alternatives`` the other
        picks in order, ``notes`` plain sentences saying what was guessed or refused."""
        guess = guess_batch(text, this_year=datetime.date.today().year)
        return {
            "description": guess.description,
            "date": guess.date.text() if guess.date else "",
            "alternatives": [a.text() for a in guess.alternatives],
            "notes": list(guess.notes),
        }

    @Slot(str, result=str)
    def validateDate(self, text: str) -> str:  # noqa: N802
        """The reason a start-page date ("1978" or "1978-07", blank allowed) is refused, or ""."""
        if not text.strip():
            return ""
        try:
            EstimatedDate.parse(text.strip())
        except ValueError as exc:
            return str(exc)
        return ""
