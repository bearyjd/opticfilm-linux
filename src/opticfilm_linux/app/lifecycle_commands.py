"""Opening, creating and closing a batch for the controller.

Kept apart from scans and long jobs. A change of batch reserves ownership under the
controller's lock (``_lifecycle_pending``), cancels a holder move that has not started, and runs
on the command worker without the state lock held.
"""

import logging
import os
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path

from opticfilm_linux.app.auto import MISSING_DESTINATION
from opticfilm_linux.app.selection import Selection
from opticfilm_linux.app.state import EMPTY_STATE, Banner
from opticfilm_linux.batch.metadata import checked_meta
from opticfilm_linux.batch.model import BatchDefaults, SlotOrientation
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.output import Format, JpegSize
from opticfilm_linux.scanner.holder_latch import HolderLatch

log = logging.getLogger(__name__)


class LifecycleCommands:
    """Mixed into ``Controller``, whose lock, session, workers and counters it uses."""

    def new_batch(
        self,
        directory: Path,
        name: str,
        album: str,
        date: str | None,
        dpi: int,
        rotation: int = 0,
        mirror: bool = False,
        slots: tuple[SlotOrientation, ...] = (),
        *,
        export_dir: str = "",
        export_format: Format = "jpeg",
        jpeg_size: JpegSize = "full",
        auto_export: bool = False,
        keep_raw: bool = False,
        collection: str = "",
        box: int = 1,
        section: str = "a",
        section_size: int = 140,
        description: str = "",
    ) -> None:
        def job() -> None:
            when, description_text = checked_meta(date, description)
            if auto_export and not export_dir.strip():
                raise BatchError(MISSING_DESTINATION)
            if export_dir:
                _check_export_directory(directory, Path(export_dir))
            defaults = BatchDefaults(
                dpi=dpi,
                rotation=rotation,
                mirror=mirror,
                slots=slots,
                export_dir=export_dir,
                export_format=export_format,
                jpeg_size=jpeg_size,
                auto_export=auto_export,
                keep_raw=keep_raw,
            )
            layout = dict(
                collection=collection, box=box, section=section, section_size=section_size
            )
            self._replace_batch(
                lambda: BatchStore.create(
                    directory, name, album, when, defaults, description=description_text, **layout
                )
            )

        self._run_lifecycle(job, "Could not create the batch")

    def open_batch(self, directory: Path, readonly: bool = False) -> None:
        def job() -> None:
            self._replace_batch(lambda: BatchStore.open(directory, readonly=readonly), readonly)

        self._run_lifecycle(job, "Could not open the batch")

    def close_batch(self) -> None:
        self._run_lifecycle(self._detach, "Could not close the batch")

    def _run_lifecycle(self, job: Callable[[], None], message: str) -> None:
        """Reserve batch ownership until the queued change has completely finished."""
        self._cancel_queued_holder()
        with self._lock:
            refused = self._holder_busy
            if not refused:
                self._lifecycle_pending += 1
        if refused:
            self._set(_banner=Banner("Wait for the eject/load to finish"))
            return
        # Draining jobs and opening/closing hardware must run without the state lock.
        future = self._run(job, message, droppable=True)
        self._track(future, self._lifecycle_finished)

    def _lifecycle_finished(self) -> None:
        with self._lock:
            self._lifecycle_pending -= 1

    def _replace_batch(self, make_store: Callable[[], BatchStore], readonly: bool = False) -> None:
        """Close the open batch, open the next; the Eject-button watcher stays stopped between."""
        self._detach(restore_watch=False)  # the next open steps it aside again at once
        try:
            store = make_store()
        except BaseException:
            self._restore_eject_watch()
            raise
        self._attach(store, readonly)

    def _detach(self, restore_watch: bool = True) -> None:
        self._stop_jobs()  # a job writes through the store, so it must finish before it closes
        current = self._session
        if current and current.runner and current.runner.scan_pending():
            self._remember_holder_latch(HolderLatch.UNKNOWN)
        with self._lock:
            session, self._session = self._session, None
            self._reset_load_phase()
        if session is not None:
            if session.poller is not None:
                self._watch_poller = session.poller  # a read-only session keeps the earlier one
                session.poller.close()
            if session.runner is not None:
                session.runner.stop()
                self._drain_events(session)  # failed scans must persist before dropping the session
            session.store.close()
            if self._eject_watch is not None:
                self._wait_scanner_idle()
        try:
            self._latch_pool.submit(lambda: None).result()
        except Exception:
            log.exception("could not finish queued holder latch persistence")
        if restore_watch:
            self._restore_eject_watch()
        with self._lock:
            self._selected = None
            self._selection = Selection()
            self._selection_order = ()
            self._manifest = None
            self._state = EMPTY_STATE if self._banner is None else self._banner_only()


def _check_export_directory(batch: Path, directory: Path) -> None:
    try:
        resolved = directory.resolve()
        batch = batch.resolve()
        for component in (directory, *directory.parents):
            if component.is_symlink() and not component.exists():
                raise BatchError(f"export folder {directory} contains a broken symbolic link")
    except (OSError, RuntimeError, ValueError) as exc:
        raise BatchError(f"cannot use export folder {directory}: {exc}") from exc
    if resolved.is_relative_to(batch):
        raise BatchError(f"export to a folder outside the batch folder, not {directory}")

    # Validate the closest existing parent without creating the user's destination.
    parent = resolved
    while True:
        try:
            mode = parent.stat().st_mode
            break
        except FileNotFoundError:
            parent = parent.parent
        except NotADirectoryError as exc:
            raise BatchError(f"export folder {directory} is not a directory") from exc
        except (OSError, ValueError) as exc:
            raise BatchError(f"cannot use export folder {directory}: {exc}") from exc
    if not stat.S_ISDIR(mode):
        raise BatchError(f"export folder {parent} is not a directory")
    if not mode & 0o222 or not os.access(parent, os.W_OK | os.X_OK):
        raise BatchError(f"export folder {parent} is not writable")
    try:
        with tempfile.TemporaryFile(dir=parent) as probe:
            probe.write(b"\0")
            probe.flush()
    except OSError as exc:
        raise BatchError(f"export folder {parent} is not writable: {exc}") from exc
