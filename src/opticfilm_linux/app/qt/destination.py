"""Debounced setup validation; filesystem probes never run on the Qt thread."""

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from opticfilm_linux.app.controller import _check_export_directory
from opticfilm_linux.app.prefs import batch_subfolder
from opticfilm_linux.batch.store import BatchError


def _problem(parent: str, name: str, destination: str) -> str:
    try:
        folder = batch_subfolder(Path(parent), name)
        _check_export_directory(folder, Path(destination))
    except (BatchError, OSError, ValueError) as exc:
        return str(exc)
    return ""


class DestinationCheck(QObject):
    changed = Signal()
    completed = Signal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._generation = 0
        self._problem = "Choose an export folder for automatic export."
        self._inputs = ("", "", "")
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="export-destination")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._start)
        self.completed.connect(self._finish)

    problem = Property(str, lambda self: self._problem, notify=changed)

    def request(self, parent: str, name: str, destination: str, problem: str = "") -> None:
        self._generation += 1
        self._timer.stop()
        self._inputs = (parent, name, destination)
        self._problem = problem or "Checking export folder…"
        self.changed.emit()
        if not problem:
            self._timer.start()

    def _start(self) -> None:
        generation = self._generation
        future = self._pool.submit(_problem, *self._inputs)
        future.add_done_callback(lambda result: self._deliver(generation, result))

    def _deliver(self, generation: int, future: Future) -> None:
        if future.cancelled():
            return
        try:
            problem = future.result()
        except Exception as exc:
            problem = f"Could not check export folder: {exc}"
        try:
            self.completed.emit(generation, problem)
        except RuntimeError:
            pass  # The window was destroyed while a filesystem probe was finishing.

    @Slot(int, str)
    def _finish(self, generation: int, problem: str) -> None:
        if generation == self._generation:
            self._problem = problem
            self.changed.emit()

    def close(self) -> None:
        self._generation += 1
        self._timer.stop()
        self._pool.shutdown(wait=False, cancel_futures=True)
