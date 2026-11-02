"""Qt-free per-tray automatic export, verification and guarded raw cleanup.

The controller runs this on its existing serial jobs worker, independently of manual export
views and cancellation. Terminal manifest states are never rearmed by later review events.
"""

import threading
from collections.abc import Callable
from pathlib import Path

from opticfilm_linux.app.raw import cleanup_pending, raw_failure, raw_inventory
from opticfilm_linux.batch.empty import is_empty_confirmed
from opticfilm_linux.batch.export import (
    CleanupPolicyRefused,
    CleanupRetryRequired,
    ExportVerificationFailed,
    _release_defaults,
    cleanup_raw,
    export_batch,
    verify_tray_export,
)
from opticfilm_linux.batch.model import SlideStatus, TrayStage
from opticfilm_linux.batch.review import accept_tray
from opticfilm_linux.batch.store import BatchError, BatchStore

READY = (TrayStage.REVIEW, TrayStage.EXPORTED)
INCOMPLETE = ("pending", "exported", "verified")
MISSING_DESTINATION = "Choose an album folder before automatic export"


class _Superseded(Exception):
    """Another action replaced the automatic progress being processed."""


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

    def _update(self, number, *, expected, **changes):
        changed = self.store.update_tray_if(number, lambda t: t.auto == expected, **changes)
        if not changed:
            raise _Superseded
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

    def retry_missing_destination(self) -> tuple[int, ...]:
        """Retry only recoverable destination failures after settings were validated."""
        manifest = self.store.snapshot()
        if (
            self.store.readonly
            or not manifest.defaults.auto_export
            or not manifest.defaults.export_dir
        ):
            return ()
        retried = []
        for tray in manifest.trays:
            changed = self.store.update_tray_if(
                tray.number,
                lambda t: (
                    t.auto == "failed"
                    and t.stage in READY
                    and not t.raw_removed
                    and t.auto_error.endswith(f". {MISSING_DESTINATION}")
                ),
                auto="pending",
                auto_error="",
                auto_failed=(),
            )
            if changed:
                retried.append(tray.number)
        return tuple(retried)

    def retry_completed_exports(self) -> tuple[int, ...]:
        """Reconcile a manual export with failed automatic progress, then verify again."""
        retried = []
        for tray in self.store.snapshot().trays:
            if tray.auto != "failed" or tray.raw_removed or tray.stage not in READY:
                continue
            accepted = [s for s in tray.slides if s.status is SlideStatus.ACCEPTED]
            if not accepted or self._owner_excluded(tray):
                continue
            if any(not s.exported or s.preview_stale for s in accepted):
                continue
            try:
                self._update(
                    tray.number, expected=tray.auto, auto="exported", auto_failed=(), auto_error=""
                )
            except _Superseded:
                continue
            retried.append(tray.number)
        return tuple(retried)

    def _eligible(self, tray) -> bool:
        if tray.stage not in READY:
            return False
        if tray.raw_removed:
            # Only completion with no remaining raw is safe to reconcile automatically.
            return tray.auto == "verified" and not cleanup_pending(self.store, tray)
        if tray.auto == "blocked":
            return not self._awaiting_review(tray) and not (
                self._owner_excluded(tray) and "blocked by your decision" in tray.auto_error
            )
        if tray.auto in INCOMPLETE:
            return True
        manifest = self.store.snapshot()
        return (
            tray.auto == "held"
            and manifest.first_tray_released
            and manifest.released_render == _release_defaults(manifest.defaults)
            and manifest.defaults.auto_export
            and not manifest.defaults.keep_raw
            and not self._unreviewed_flags(tray)
        )

    def run(self, number: int) -> None:
        tray = self._tray(number)
        if not self._eligible(tray):
            return
        if self.cancel.is_set():
            return
        if not self.store.snapshot().defaults.auto_export and not tray.raw_removed:
            if tray.auto == "pending":
                try:
                    self._update(number, expected="pending", auto="off")
                except _Superseded:
                    pass
            return
        before = None
        try:
            before = raw_inventory(self.store, (number,))
            if tray.auto in ("pending", "blocked") and not self._export_pending(
                number, before=before, expected=tray.auto
            ):
                return
            expected = "exported" if tray.auto in ("pending", "blocked") else tray.auto
            self._verify_and_finish(number, expected=expected, before=before)
        except _Superseded:
            return
        except Exception as exc:
            self._failed(number, exc, before=before, expected=tray.auto)

    def _accept_pending(self, number: int, *, expected: str) -> None:
        tray = self._tray(number)
        flagged = tuple(
            sorted(
                {
                    s.position
                    for s in tray.slides
                    if (
                        s.position in tray.auto_flagged
                        or s.flags
                        or s.status in (SlideStatus.NEEDS_LOOK, SlideStatus.BLANK)
                    )
                }
            )
        )
        # Remember flags before automatic acceptance changes undecided statuses.
        self._update(number, expected=expected, auto_flagged=flagged)
        accept_tray(self.store, number, automatic=True)

    def _export_pending(self, number: int, *, before, expected: str) -> bool:
        self._accept_pending(number, expected=expected)
        if any(s.preview_stale for s in self._tray(number).slides):
            return False
        if self._blocked(number, expected=expected):
            return False
        defaults = self.store.snapshot().defaults
        if not defaults.export_dir:
            raise BatchError(MISSING_DESTINATION)
        failed, errors = [], []
        for slide in self._tray(number).slides:
            if self.cancel.is_set():
                return False
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
                    return False
            except Exception as exc:
                failed.append(slide.position)
                errors.append(f"Slide {slide.position}: {exc}")
        if failed:
            outcome = self._failure_outcome(number, before)
            self._update(
                number,
                expected=expected,
                auto="failed",
                auto_failed=tuple(failed),
                auto_error=outcome + ". " + "; ".join(errors),
            )
            return False
        self._update(number, expected=expected, auto="exported")
        return True

    def _failed(self, number: int, exc: Exception, *, before, expected=None) -> None:
        expected = self._tray(number).auto if expected is None else expected
        try:
            self._record_failure(number, exc, before=before, expected=expected)
        except _Superseded:
            return

    def _record_failure(self, number, exc, *, before, expected) -> None:
        if isinstance(exc, CleanupRetryRequired) and not cleanup_pending(
            self.store, self._tray(number)
        ):
            self._update(
                number, expected=expected, auto="verified", auto_error=str(exc), auto_failed=()
            )
            return
        if isinstance(exc, CleanupPolicyRefused) and not self._tray(number).raw_removed:
            self._update(
                number, expected=expected, auto="held", auto_error=str(exc), auto_failed=()
            )
            return
        positions = (
            (exc.position,)
            if isinstance(exc, ExportVerificationFailed)
            else tuple(
                s.position
                for s in self._tray(number).slides
                if s.status is SlideStatus.ACCEPTED and not s.exported
            )
        )
        outcome = self._failure_outcome(number, before)
        self._update(
            number,
            expected=expected,
            auto="failed",
            auto_error=f"{outcome}. {exc}",
            auto_failed=positions,
        )

    def _failure_outcome(self, number, before) -> str:
        if before is None:
            return "raw cleanup outcome unknown (could not inspect raw files before the job)"
        try:
            after = raw_inventory(self.store, (number,))
            return raw_failure(self.store, (number,), before, after)
        except Exception as evidence_error:
            return f"raw cleanup outcome unknown (could not inspect raw files: {evidence_error})"

    def _unreviewed_flags(self, tray) -> bool:
        return any(
            not is_empty_confirmed(tray, s)
            if s.status is SlideStatus.BLANK
            else (s.position in tray.auto_flagged or s.flags) and not s.owner_decided
            for s in tray.slides
        )

    def _owner_excluded(self, tray) -> tuple[int, ...]:
        return tuple(
            s.position
            for s in tray.slides
            if s.owner_decided and s.status is SlideStatus.NEEDS_LOOK
        )

    def _awaiting_review(self, tray) -> tuple[int, ...]:
        return tuple(
            s.position
            for s in tray.slides
            if s.status not in (SlideStatus.ACCEPTED, SlideStatus.BLANK) and not s.owner_decided
        )

    def _blocked(self, number: int, *, expected=None) -> bool:
        tray = self._tray(number)
        expected = tray.auto if expected is None else expected
        held = self._awaiting_review(tray)
        if not held:
            excluded = self._owner_excluded(tray)
            if excluded:
                self._update(
                    number,
                    expected=expected,
                    auto="blocked",
                    auto_failed=(),
                    auto_error="Owner excluded slides "
                    + ", ".join(map(str, excluded))
                    + "; blocked by your decision; raw scans kept",
                )
            return bool(excluded)
        self._update(
            number,
            expected=expected,
            auto="blocked",
            auto_failed=(),
            auto_error="Waiting for owner review of slides " + ", ".join(map(str, held)),
        )
        return True

    def _hold_empty_frames(self, number: int, *, expected=None) -> bool:
        tray = self._tray(number)
        expected = tray.auto if expected is None else expected
        empty = sum(
            s.status is SlideStatus.BLANK and not is_empty_confirmed(tray, s) for s in tray.slides
        )
        if empty:
            self._update(
                number,
                expected=expected,
                auto="held",
                auto_failed=(),
                auto_error=(f"{empty} frame(s) look empty - confirm they are empty to free raw"),
            )
        return bool(empty)

    def _verify_and_finish(
        self, number: int, *, release: bool = False, expected=None, before=None
    ) -> None:
        if self.cancel.is_set():
            return
        tray = self._tray(number)
        expected = tray.auto if expected is None else expected
        if tray.auto != expected or expected not in (*INCOMPLETE, "held", "blocked"):
            return
        if cleanup_pending(self.store, tray):
            return
        try:
            before = raw_inventory(self.store, (number,)) if before is None else before
            if tray.raw_removed:
                cleanup_raw(self.store, number)
                self._cleanup_finished(number, expected=expected)
                return
            if self._blocked(number, expected=expected) or self._hold_empty_frames(
                number, expected=expected
            ):
                return
            if any(s.preview_stale for s in self._tray(number).slides):
                self._update(
                    number,
                    expected=expected,
                    auto="held",
                    auto_error="Waiting for updated previews",
                )
                return
            verify_tray_export(self.store, number)
            self._update(number, expected=expected, auto="verified", auto_error="", auto_failed=())
            expected = "verified"
            self._finish_verified(number, release=release)
        except _Superseded:
            return
        except Exception as exc:
            self._failed(number, exc, before=before, expected=expected)

    def _finish_verified(self, number: int, *, release: bool) -> None:
        manifest = self.store.snapshot()
        tray = self._tray(number)
        if tray.auto != "verified" or cleanup_pending(self.store, tray):
            return
        if not self._cleanup_permitted(manifest, tray, release):
            reason = self._cleanup_hold_reason(manifest, tray)
            self._update(
                number, expected="verified", auto="held", auto_error=reason, auto_failed=()
            )
            return
        if self.cancel.is_set():
            return
        tray = self._tray(number)
        if cleanup_pending(self.store, tray):
            return
        if not tray.raw_removed:
            cleanup_raw(self.store, number, release_first=release)
        self._cleanup_finished(number, expected="verified")

    def _cleanup_finished(self, number: int, *, expected: str) -> None:
        if self._tray(number).auto == "freed":
            self.on_update()
        else:
            self._update(number, expected=expected, auto="freed", auto_error="", auto_failed=())

    def _cleanup_hold_reason(self, manifest, tray) -> str:
        if self._unreviewed_flags(tray):
            return "Waiting for owner review of flagged slides"
        if manifest.defaults.keep_raw:
            return "Raw scans kept by batch settings"
        return "Waiting for first-tray raw removal approval"

    def _cleanup_permitted(self, manifest, tray, release: bool) -> bool:
        return (
            manifest.defaults.auto_export
            and not manifest.defaults.keep_raw
            and not self._unreviewed_flags(tray)
            and (
                release
                or (
                    manifest.first_tray_released
                    and manifest.released_render == _release_defaults(manifest.defaults)
                )
            )
        )

    def _release_candidate(self, manifest):
        first = min(manifest.trays, key=lambda t: t.number)
        confirmed_empty = bool(first.slides) and all(
            s.status is SlideStatus.BLANK and is_empty_confirmed(first, s) for s in first.slides
        )
        if not first.raw_removed and not confirmed_empty:
            return first.number
        return next(
            (
                t.number
                for t in sorted(manifest.trays, key=lambda t: t.number)
                if self._approval_ready(t)
            ),
            None,
        )

    def _approval_ready(self, tray) -> bool:
        return (
            not tray.raw_removed
            and tray.stage is TrayStage.EXPORTED
            and tray.auto == "held"
            and not self._unreviewed_flags(tray)
            and not any(s.preview_stale for s in tray.slides)
            and any(s.status is SlideStatus.ACCEPTED and s.exported for s in tray.slides)
            and all(
                s.status is SlideStatus.BLANK or (s.status is SlideStatus.ACCEPTED and s.exported)
                for s in tray.slides
            )
        )

    def confirm_free_held(self, number: int) -> None:
        manifest = self.store.snapshot()
        tray = self._tray(number)
        if (
            number != self._release_candidate(manifest)
            or tray.stage not in READY
            or tray.auto != "held"
            or tray.raw_removed
        ):
            return
        before = None
        try:
            before = raw_inventory(self.store, (number,))
            if self._blocked(number, expected=tray.auto) or self._unreviewed_flags(
                self._tray(number)
            ):
                return
            self._verify_and_finish(number, release=True, expected=tray.auto, before=before)
        except _Superseded:
            return
        except Exception as exc:
            self._failed(number, exc, before=before, expected=tray.auto)
