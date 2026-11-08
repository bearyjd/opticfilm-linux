"""Review, export and automatic-job commands for the window controller."""

from __future__ import annotations

import dataclasses
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from opticfilm_linux.app.holder import WAIT_BATCH
from opticfilm_linux.app.messages import meta_update_text
from opticfilm_linux.app.raw import cleanup_pending
from opticfilm_linux.app.state import Banner
from opticfilm_linux.batch.export import clear_exported
from opticfilm_linux.batch.layout import advance_section
from opticfilm_linux.batch.meta_sync import (
    MetaUpdateProgress,
    out_of_date_keys,
    update_exported_metadata,
)
from opticfilm_linux.batch.model import Mode, SlideSettings, SlideStatus
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.output import Format, JpegSize

if TYPE_CHECKING:
    from opticfilm_linux.app.controller import _Session


class ReviewCommands:
    def scan(self) -> None:
        with self._lock:
            skipped = self._take_scan_openings()  # fixed now: a later choice cannot change it
        self._reserved_scan(
            lambda runner: runner.scan_tray(skipped=skipped), "Could not start a scan"
        )

    def rescan(self, number: int) -> None:
        def job(runner: BatchRunner) -> None:
            with self._lock:
                if number in self._auto_active:
                    raise BatchError("Wait for this tray’s automatic export to finish")
            self._drop_progress(number)
            return runner.rescan_tray(number)

        self._reserved_scan(job, f"Could not re-scan tray {number}")

    def set_tray_mode(self, number: int, mode: Mode) -> None:
        def job(runner: BatchRunner) -> None:
            with self._lock:
                if number in self._auto_active:
                    raise BatchError("Wait for this tray’s automatic export to finish")
            runner.set_tray_mode(number, mode)

        self._run_writer(job, f"Could not change mode of tray {number}")

    def _section_refusal(self, reserved: bool = False) -> str | None:
        """Called under the controller lock before reserving the transition."""
        if self._lifecycle_pending or self._closing.is_set():
            return WAIT_BATCH
        session = self._session
        runner = session.runner if session is not None else None
        if self._holder_busy:
            return "Wait for the eject/load to finish"
        if self._scans_queued > int(reserved) or (runner is not None and runner.scan_pending()):
            return "Wait for the scan or section change to finish"
        if self._long_jobs or self._job_futures:
            return "Wait for the export or disk-space job to finish"
        if self._updating or self._enhancing or (runner is not None and runner.work_pending()):
            return "Wait for enhancement to finish"
        return None

    def finish_box(self, next_box: bool = True) -> None:
        with self._lock:
            refusal = self._section_refusal()
            if refusal is None:
                self._scans_queued += 1  # reserve while queued, including against holder moves
        if refusal is not None:
            self._set(_banner=Banner(refusal))
            return
        reserved = True

        def release() -> None:
            nonlocal reserved
            with self._lock:
                if reserved:
                    self._scans_queued -= 1
                    reserved = False

        def job(_runner: BatchRunner) -> None:
            try:
                with self._lock:
                    refusal = self._section_refusal(reserved=True)
                    session = self._session
                if refusal is not None:
                    raise BatchError(refusal)
                if session is None:
                    raise BatchError("no batch is open")
                advance_section(session.store, next_box=next_box)
                self._set(_selected=None)
            finally:
                release()

        future = None
        try:
            future = self._run_writer(job, "Could not finish the section")
        finally:
            self._track(future, release)  # also release when writer validation skips the job

    def select_tray(self, number: int) -> None:
        self._set(_selected=number)

    # -- review --------------------------------------------------------------------------

    def set_status(self, number: int, position: int, status: SlideStatus) -> None:
        def job(runner: BatchRunner) -> None:
            runner.set_slide_status(number, position, status)
            session = self._session
            if session is not None:
                with self._lock:
                    self._auto_due.add(number)
                self._submit_auto_due()

        self._run_writer(job, f"Could not change slide {position} of tray {number}")

    def confirm_empty_frames(
        self, number: int, expected_positions, *, expected_fingerprints, expected_sweep_version: int
    ) -> None:
        expected_positions = tuple(expected_positions)
        expected_fingerprints = dict(expected_fingerprints)

        def job(runner: BatchRunner) -> None:
            from opticfilm_linux.batch.review import confirm_empty_frames

            session = self._session
            if session is None:
                return
            confirm_empty_frames(
                session.store,
                number,
                expected_positions,
                expected_fingerprints=expected_fingerprints,
                expected_sweep_version=expected_sweep_version,
            )
            with self._lock:
                self._auto_due.add(number)
            self._submit_auto_due()

        self._run_writer(job, f"Could not confirm empty frames of tray {number}")

    def edit_slide(self, number: int, position: int, settings: SlideSettings) -> None:
        self._run_writer(
            lambda runner: self._edit(runner, number, position, settings),
            f"Could not adjust slide {position} of tray {number}",
        )

    def adjust_slide(
        self, number: int, position: int, changes: Mapping[str, float | int | None]
    ) -> None:
        """Change only the named ``SlideSettings`` fields, merged with what is stored now."""

        def job(runner: BatchRunner) -> None:
            current = self._slide_settings(number, position)
            self._edit(runner, number, position, dataclasses.replace(current, **changes))

        self._run_writer(job, f"Could not adjust slide {position} of tray {number}")

    def rotate(self, number: int, position: int, quarter_turns: int) -> None:
        def job(runner: BatchRunner) -> None:
            current = self._slide_settings(number, position)
            # the slide is flipped after it is turned: on screen, a flipped turn runs backwards
            step = -90 if current.mirror else 90
            turned = (current.rotation + step * quarter_turns) % 360
            self._edit(runner, number, position, dataclasses.replace(current, rotation=turned))

        self._run_writer(job, f"Could not rotate slide {position} of tray {number}")

    def mirror(self, number: int, position: int) -> None:
        """Flip the slide left-right, or back: toggles what is stored when the job runs."""

        def job(runner: BatchRunner) -> None:
            current = self._slide_settings(number, position)
            flipped = dataclasses.replace(current, mirror=not current.mirror)
            self._edit(runner, number, position, flipped)

        self._run_writer(job, f"Could not mirror slide {position} of tray {number}")

    def set_batch_orientation(self, rotation: int, mirror: bool) -> None:
        """Turn and flip every slide of the batch; trays that cannot follow are named."""
        from opticfilm_linux.app import controller

        def job(runner: BatchRunner) -> None:
            manifest = self._open_store().snapshot()
            defaults = manifest.defaults
            if (
                manifest.first_tray_released
                and self._automatic_batch(manifest)
                and (rotation, mirror) != (defaults.rotation, defaults.mirror)
            ):
                raise BatchError("Batch orientation cannot change after releasing the first tray")
            skipped = runner.set_batch_orientation(rotation, mirror)
            if skipped:
                self._set(_banner=Banner(controller._skipped_text(skipped)))

        self._run_writer(job, "Could not change the batch orientation")

    def reset_slide(self, number: int, position: int) -> None:
        self.edit_slide(number, position, SlideSettings())

    def accept_all(self) -> None:
        def job(runner: BatchRunner) -> None:
            runner.accept_all()
            session = self._session
            if session is not None:
                blocked = tuple(
                    t.number
                    for t in session.store.snapshot().trays
                    if t.auto == "blocked" and self._auto_pipeline(session.store)._eligible(t)
                )
                with self._lock:
                    self._auto_due.update(blocked)
                self._submit_auto_due()

        self._run_writer(job, "Could not accept all slides")

    def _edit(
        self, runner: BatchRunner, number: int, position: int, settings: SlideSettings
    ) -> None:
        key = (number, position)
        with self._lock:
            self._updating = self._updating | {key}
        try:
            runner.edit_slide(number, position, settings)
        except Exception:
            with self._lock:
                self._updating = self._updating - {key}
            raise

    def _slide_settings(self, number: int, position: int) -> SlideSettings:
        session = self._session
        if session is None:
            raise BatchError("no batch is open")
        for tray in session.store.snapshot().trays:
            for slide in tray.slides:
                if (tray.number, slide.position) == (number, position):
                    return slide.settings
        raise BatchError(f"no slide {position} in tray {number}")

    # -- export and free disk space ------------------------------------------------------

    def set_export_settings(self, directory: Path, fmt: Format, jpeg_size: JpegSize) -> None:
        def job(_runner: BatchRunner) -> None:
            self._save_export_settings(directory, fmt, jpeg_size)
            self._run_writer(retry, "Could not resume automatic export", pool=self._jobs)

        def retry(_runner: BatchRunner) -> None:
            # Finish any active automatic job before rearming its failed tray.
            session = self._session
            if session is not None and session.auto_export:
                retry = self._auto_pipeline(session.store).retry_missing_destination()
                with self._lock:
                    self._auto_due.update(retry)
                self._submit_auto_due()

        self._run_writer(job, "Could not save export settings")

    @staticmethod
    def _automatic_batch(manifest) -> bool:
        return manifest.defaults.auto_export or any(
            tray.auto not in ("", "off") for tray in manifest.trays
        )

    def _save_export_settings(self, directory: Path, fmt: Format, jpeg_size: JpegSize) -> None:
        from opticfilm_linux.app import controller

        store = self._open_store()
        controller._check_export_directory(store.directory, directory)

        def change(manifest):
            defaults = dataclasses.replace(
                manifest.defaults, export_dir=str(directory), export_format=fmt, jpeg_size=jpeg_size
            )
            if (
                manifest.first_tray_released
                and self._automatic_batch(manifest)
                and defaults != manifest.defaults
            ):
                raise BatchError("Export settings cannot change after releasing the first tray")
            return dataclasses.replace(manifest, defaults=defaults)

        store.update(change)

    def export(self, directory: Path, fmt: Format, jpeg_size: JpegSize = "full") -> None:
        from opticfilm_linux.app import controller

        def job(_runner: BatchRunner) -> None:
            controller.run_export(
                self._open_store(),
                directory,
                fmt,
                self._cancel_export,
                lambda view: self._set(_export=view),
                jpeg_size=jpeg_size,
            )
            session = self._session
            if session is not None and session.auto_export and not self._cancel_export.is_set():
                retry = self._auto_pipeline(session.store).retry_completed_exports()
                with self._lock:
                    self._auto_due.update(retry)
                self._submit_auto_due()

        if not self._closing.is_set():
            self._cancel_export.clear()  # at submit: a queued job must not undo a later cancel
        self._after_pending_commands(
            job,
            "Could not export",
            prepare=lambda _runner: self._save_export_settings(directory, fmt, jpeg_size),
        )

    def reexport_all_accepted(self) -> None:
        """After confirmation, let the next export rewrite accepted slides."""

        def job(_runner: BatchRunner) -> None:
            clear_exported(self._open_store())
            self._set(_export=None)

        self._after_pending_commands(job, "Could not prepare re-export")

    def update_exported_metadata(self) -> None:
        """The owner's "Update JPEGs" button: put the current description and date into every
        exported JPEG that does not carry them yet, without re-encoding."""
        self._queue_metadata_update(automatic=False)

    def _queue_metadata_update_if_due(self) -> None:
        """Automatic update: only if the setting is on, the batch is writable and some JPEG is
        out of date. A job already waiting covers any edit made before it starts."""
        with self._lock:
            session = self._session
            if not self._update_metadata or session is None or session.runner is None:
                return
            if self._meta_update_queued:
                return
        if out_of_date_keys(session.store.snapshot()):
            self._queue_metadata_update(automatic=True)

    def _queue_metadata_update(self, automatic: bool) -> None:
        if automatic:
            with self._lock:
                if self._meta_update_queued:
                    return
                self._meta_update_queued = True

        def progress(update: MetaUpdateProgress) -> None:
            self._set(_banner=Banner(f"Updating JPEGs: {update.done} of {update.total}"))
            self._rebuild()

        def job(runner: BatchRunner) -> None:
            if automatic:
                with self._lock:
                    self._meta_update_queued = False
                    if not self._update_metadata:
                        return  # switched off while it waited
                    held = runner.scan_pending() or self._scans_queued
                    self._meta_update_due = bool(held)
                if held:
                    return  # a scan ends -> ``_drain_events`` asks again
            store = self._open_store()
            if automatic and not out_of_date_keys(store.snapshot()):
                return  # an earlier update already did it
            self._set(_banner=Banner("Updating the description and date in exported JPEGs"))
            self._rebuild()
            result = update_exported_metadata(store, on_progress=progress, cancel=self._closing)
            self._set(_banner=Banner(*meta_update_text(result)))
            self._rebuild()

        def not_queued() -> None:
            if automatic:
                with self._lock:
                    self._meta_update_queued = False

        # ``_after_pending_commands`` runs the job on the jobs worker, the one that exports and
        # automatic exports use: an update never overlaps an export, whichever tray.
        self._after_pending_commands(
            job,
            "Could not update the exported JPEGs",
            on_finished=not_queued if automatic else None,
        )

    def _resume_metadata_update(self) -> None:
        """No scan is running any more: run the automatic update one held back."""
        with self._lock:
            runner = self._session.runner if self._session else None
            if self._scans_queued or (runner is not None and runner.scan_pending()):
                return  # still scanning: stay due
            due, self._meta_update_due = self._meta_update_due, False
        if due:
            self._queue_metadata_update_if_due()

    def cancel_export(self) -> None:
        self._cancel_export.set()

    def dismiss_export(self) -> None:
        self._set(_export=None)

    def request_free_space(self) -> None:
        from opticfilm_linux.app import controller

        self._after_pending_commands(
            lambda _runner: self._set(
                _free_space=controller.free_space_candidates(self._open_store())
            ),
            "Could not check disk space",
        )

    def confirm_free_space(self) -> None:
        """Remove the raw scans of exactly the trays the owner was shown, once."""
        from opticfilm_linux.app import controller

        def job(_runner: BatchRunner) -> None:
            view = self._free_space
            if view is None or not view.trays:
                raise BatchError("check which trays can be freed before removing anything")
            if view.running or view.freed is not None or view.message:
                return  # a second click: already running or done
            session = self._session
            try:
                controller.run_free_space(
                    self._open_store(),
                    self._cancel_free,
                    lambda update: self._set(_free_space=update),
                    tuple(number for number, _ in view.trays),
                    release_first=False,
                )
            finally:
                if session is not None:
                    self._resume_auto_backlog(session)

        if not self._closing.is_set():
            self._cancel_free.clear()
        self._after_pending_commands(job, "Could not free disk space")

    def _after_pending_commands(
        self,
        job: Callable[[BatchRunner], None],
        message: str,
        prepare: Callable[[BatchRunner], None] | None = None,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        """Queue a long job behind every command already submitted (an edit made a moment
        ago must be stored before an export reads the slides), then run it on the jobs worker."""
        with self._lock:
            self._long_jobs += 1  # count the job before it reaches the jobs worker

        handed_over = threading.Event()

        def handover(runner: BatchRunner) -> None:
            if prepare is not None:
                prepare(runner)
            future = self._run_writer(job, message, pool=self._jobs)
            handed_over.set()
            if on_finished is not None:
                self._track(future, on_finished)

        def finished() -> None:
            self._long_job_handed_over()
            if on_finished is not None and not handed_over.is_set():
                on_finished()

        future = self._run_writer(handover, message)
        self._track(future, finished)

    def _long_job_handed_over(self) -> None:
        with self._lock:
            self._long_jobs -= 1

    def cancel_free_space(self) -> None:
        self._cancel_free.set()

    def dismiss_free_space(self) -> None:
        self._set(_free_space=None)

    def _open_store(self) -> BatchStore:
        session = self._session
        if session is None:
            raise BatchError("no batch is open")
        return session.store

    def _auto_pipeline(self, store: BatchStore):
        from opticfilm_linux.app import controller

        return controller.AutoPipeline(store, cancel=self._cancel_auto, on_update=self._rebuild)

    def _submit_auto_due(self) -> None:
        """Leave the event lock before handing any job to the command/jobs workers."""
        with self._lock:
            session = self._session
            due, self._auto_due = self._auto_due, set()
            if session is None or session.runner is None or self._cancel_auto.is_set():
                return
            if not due:
                return
        if not session.auto_export:

            def filter_due(_runner: BatchRunner) -> None:
                eligible = {
                    t.number
                    for t in session.store.snapshot().trays
                    if t.raw_removed
                    and t.auto == "verified"
                    and not cleanup_pending(session.store, t)
                }
                self._schedule_auto(session, due & eligible)

            self._run_writer(filter_due, "Could not check automatic cleanup")
            return
        self._schedule_auto(session, due)

    def _schedule_auto(self, session: _Session, due: set[int]) -> None:
        with self._lock:
            if self._session is not session or self._cancel_auto.is_set():
                return
            self._auto_due.update(due & self._auto_active)
            due -= self._auto_active
            self._auto_active.update(due)
        for number in sorted(due):
            self._queue_auto(session, number)

    def _queue_auto(self, session: _Session, number: int, release: bool = False) -> None:
        started = threading.Event()

        def job(_runner: BatchRunner) -> None:
            started.set()
            if self._session is not session or self._cancel_auto.is_set():
                return
            pipeline = self._auto_pipeline(session.store)
            if release:
                pipeline.confirm_free_held(number)
                self._resume_auto_backlog(session)
            else:
                pipeline.arm(number)
                pipeline.run(number)
            tray = next(t for t in session.store.snapshot().trays if t.number == number)
            self._retry_auto_cleanup(session, tray)
            if tray.auto == "failed":
                self._set(
                    _banner=Banner(
                        f"Tray {number}: automatic export failed; "
                        f"{tray.auto_error.partition('. ')[0]}",
                        tray.auto_error,
                    )
                )
            self._rebuild()

        def release_reservation() -> None:
            with self._lock:
                if self._session is session:
                    self._auto_active.discard(number)
            if started.is_set():
                self._submit_auto_due()

        self._after_pending_commands(
            job, "Could not complete automatic export", on_finished=release_reservation
        )

    def _retry_auto_cleanup(self, session, tray) -> None:
        """Retry one transient commit race per tray, leaving persistent errors visible."""
        retry = (
            tray.auto == "verified" and tray.auto_error and not cleanup_pending(session.store, tray)
        )
        with self._lock:
            if self._session is not session:
                return
            if retry:
                if tray.number not in self._auto_retried:
                    self._auto_retried.add(tray.number)
                    self._auto_due.add(tray.number)
            elif not tray.auto_error:
                self._auto_retried.discard(tray.number)

    def _resume_auto_backlog(self, session: _Session) -> None:
        if not session.store.snapshot().first_tray_released:
            return
        backlog = self._auto_pipeline(session.store).resume()
        with self._lock:
            if self._session is session:
                self._auto_due.update(backlog)
        self._submit_auto_due()

    def request_held_release(self, number: int, ready: Callable[[int], None]) -> None:
        """Prepare the raw-byte estimate on the controller worker before owner approval."""
        from opticfilm_linux.app.raw import raw_inventory

        def job(_runner: BatchRunner) -> None:
            store = self._open_store()
            if self.state().held_tray != number:
                return
            ready(sum(raw_inventory(store, (number,)).values()))

        self._after_pending_commands(job, f"Could not prepare raw cleanup of tray {number}")

    def confirm_free_held(self, number: int) -> None:
        """The owner's one explicit release of the checked first tray."""
        with self._lock:
            session = self._session
            if session is None or session.runner is None:
                return
            busy = number in self._auto_active
            if not busy:
                self._auto_active.add(number)
        if busy:
            self._set(
                _banner=Banner(
                    f"Tray {number}: wait for automatic export to finish, then free raw scans again"
                )
            )
            return
        self._queue_auto(session, number, release=True)

    def recover_interrupted_cleanup(self, number: int) -> None:
        """Restore intact raw scans to review after the owner's explicit confirmation."""

        def job(_runner: BatchRunner) -> None:
            from opticfilm_linux.batch.export import recover_interrupted_cleanup

            recover_interrupted_cleanup(self._open_store(), number)
            with self._lock:
                self._auto_due.add(number)
            self._submit_auto_due()
            self._rebuild()

        self._after_pending_commands(job, f"Could not recover raw scans of tray {number}")
