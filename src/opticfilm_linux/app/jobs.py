"""Long-running jobs (export, free disk space) that report progress as immutable views."""

import threading
from collections.abc import Callable, Collection
from pathlib import Path

from opticfilm_linux.app.state import ExportView, FreeSpaceView
from opticfilm_linux.batch.export import (
    FreeableTray,
    export_batch,
    free_space,
    freeable_trays,
)
from opticfilm_linux.batch.store import BatchStore
from opticfilm_linux.output import Format


def run_export(
    store: BatchStore,
    directory: Path,
    fmt: Format,
    cancel: threading.Event,
    publish: Callable[[ExportView], None],
) -> None:
    """Export accepted slides; always ends by publishing a finished view, then re-raises errors."""
    last = ExportView(running=True, done=0, total=0)
    publish(last)

    def progress(update) -> None:
        nonlocal last
        last = ExportView(running=True, done=update.done, total=update.total)
        publish(last)

    try:
        result = export_batch(store, directory, fmt, on_progress=progress, cancel=cancel)
    except BaseException:
        publish(ExportView(False, last.done, last.total, finished=True, written=last.done))
        raise
    publish(
        ExportView(
            running=False,
            done=last.done,
            total=last.total,
            finished=True,
            written=len(result.written),
            cancelled=result.cancelled,
        )
    )


def free_space_candidates(store: BatchStore) -> FreeSpaceView:
    found = freeable_trays(store)
    return _candidates_view(found)


def _candidates_view(found: tuple[FreeableTray, ...]) -> FreeSpaceView:
    return FreeSpaceView(
        trays=tuple((f.number, f.bytes) for f in found),
        total_bytes=sum(f.bytes for f in found),
    )


def run_free_space(
    store: BatchStore,
    cancel: threading.Event,
    publish: Callable[[FreeSpaceView], None],
    numbers: Collection[int],
) -> None:
    """Remove the raw scans of the confirmed trays; the final view carries the bytes freed."""
    base = _candidates_view(tuple(f for f in freeable_trays(store) if f.number in numbers))
    total = len(base.trays)

    def progress(done: int, _total: int) -> None:
        publish(FreeSpaceView(base.trays, base.total_bytes, running=True, done=done, total=total))

    publish(FreeSpaceView(base.trays, base.total_bytes, running=True, done=0, total=total))
    try:
        freed = free_space(store, on_progress=progress, cancel=cancel, only=numbers)
    except BaseException:
        publish(FreeSpaceView(base.trays, base.total_bytes, running=False, freed=0))
        raise
    publish(FreeSpaceView(base.trays, base.total_bytes, running=False, total=total, freed=freed))
