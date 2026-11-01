"""Qt-free per-tray automatic export, verification and guarded raw cleanup.

The controller runs this on its existing serial jobs worker, independently of manual export
views and cancellation. Terminal manifest states are never rearmed by later review events.
"""

import dataclasses
import threading
from collections.abc import Callable
from pathlib import Path

from opticfilm_linux.batch.export import (
    ExportVerificationFailed,
    cleanup_raw,
    export_batch,
    verify_tray_export,
)
from opticfilm_linux.batch.model import SlideStatus, TrayStage
from opticfilm_linux.batch.review import accept_tray
from opticfilm_linux.batch.store import BatchError, BatchStore

READY = (TrayStage.REVIEW, TrayStage.EXPORTED)
INCOMPLETE = ("pending", "exported", "verified")


class AutoPipeline:
    def __init__(
        self,
        store: BatchStore,
        cancel: threading.Event | None = None,
        on_update: Callable[[], None] | None = None,
    ):
        self.store = store
        self.cancel = cancel or threading.Event()
        self.on_update = on_update or (lambda: None)

    def _tray(self, number):
        return next(t for t in self.store.snapshot().trays if t.number == number)

    def _update(self, number, **changes):
        self.store.update_tray(number, **changes)
        self.on_update()

    def arm(self, number: int) -> bool:
        manifest = self.store.snapshot()
        enabled = manifest.defaults.auto_export
        changed = self.store.update_tray_if(
            number,
            lambda t: not t.auto and t.stage in READY and not t.raw_removed,
            auto="pending" if enabled else "off",
        )
        return changed and enabled

    def resume(self) -> tuple[int, ...]:
        if self.store.readonly:
            return ()
        for tray in self.store.snapshot().trays:
            if not tray.auto and tray.stage in READY:
                self.arm(tray.number)
        return tuple(t.number for t in self.store.snapshot().trays if self._eligible(t))

    def _eligible(self, tray) -> bool:
        if tray.stage not in READY:
            return False
        if tray.raw_removed:
            # Cleanup commits the removed flag before unlinking; a crash can leave sweep
            # folders behind. Only this verified cleanup state may finish after recovery.
            return tray.auto == "verified"
        if tray.auto in INCOMPLETE:
            return True
        manifest = self.store.snapshot()
        return (
            tray.auto == "held"
            and manifest.first_tray_released
            and manifest.defaults.auto_export
            and not manifest.defaults.keep_raw
            and tray.number != min(t.number for t in manifest.trays)
        )

    def run(self, number: int) -> None:
        tray = self._tray(number)
        if not self._eligible(tray):
            return
        if self.cancel.is_set():
            return
        if not self.store.snapshot().defaults.auto_export:
            self._update(number, auto="off")
            return
        try:
            if tray.auto == "pending":
                flagged = tuple(
                    sorted(
                        set(tray.auto_flagged)
                        | {
                            s.position
                            for s in tray.slides
                            if s.flags or s.status in (SlideStatus.BLANK, SlideStatus.NEEDS_LOOK)
                        }
                    )
                )
                # Persist flagged positions before acceptance removes the BLANK/NEEDS_LOOK status.
                self._update(number, auto_flagged=flagged)
                accept_tray(self.store, number)
                failed = []
                errors = []
                defaults = self.store.snapshot().defaults
                if not defaults.export_dir:
                    raise BatchError("Choose an album folder before automatic export")
                for slide in self._tray(number).slides:
                    if self.cancel.is_set():
                        return
                    try:
                        result = export_batch(
                            self.store,
                            Path(defaults.export_dir),
                            "jpeg",
                            cancel=self.cancel,
                            keys=frozenset({(number, slide.position)}),
                            jpeg_size=defaults.jpeg_size,
                        )
                        if result.cancelled:
                            return
                    except Exception as exc:
                        failed.append(slide.position)
                        errors.append(f"Slide {slide.position}: {exc}")
                if failed:
                    self._update(
                        number,
                        auto="failed",
                        auto_failed=tuple(failed),
                        auto_error="; ".join(errors),
                    )
                    return
                self._update(number, auto="exported")
            self._verify_and_finish(number)
        except Exception as exc:
            self._failed(number, exc)

    def _failed(self, number: int, exc: Exception) -> None:
        positions = (
            (exc.position,)
            if isinstance(exc, ExportVerificationFailed)
            else tuple(s.position for s in self._tray(number).slides if not s.exported)
        )
        self._update(number, auto="failed", auto_error=str(exc), auto_failed=positions)

    def _verify_and_finish(self, number: int, *, release: bool = False) -> None:
        if self.cancel.is_set():
            return
        verify_tray_export(self.store, number)
        self._update(number, auto="verified")
        manifest = self.store.snapshot()
        first = min(t.number for t in manifest.trays)
        permitted = (
            manifest.defaults.auto_export
            and not manifest.defaults.keep_raw
            and (manifest.first_tray_released or (release and number == first))
        )
        if not permitted:
            self._update(number, auto="held")
            return
        if self.cancel.is_set():
            return
        # Recheck every mark and geometry after progress callbacks and before irreversible work.
        verify_tray_export(self.store, number)
        if release and number == first:
            # This is the owner's durable decision after successful verification. Store it
            # before deletion, so a crash after unlink cannot strand the batch's first hold.
            self.store.update(lambda m: dataclasses.replace(m, first_tray_released=True))
        tray = self._tray(number)
        if not tray.raw_removed or any(self.store.tray_dir(number).glob("sweep-*")):
            cleanup_raw(self.store, number)
        self._update(number, auto="freed")

    def confirm_free_held(self, number: int) -> None:
        manifest = self.store.snapshot()
        if number != min(t.number for t in manifest.trays) or self._tray(number).auto != "held":
            return
        try:
            self._verify_and_finish(number, release=True)
        except Exception as exc:
            self._failed(number, exc)
