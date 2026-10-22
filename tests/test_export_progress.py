import shutil
import threading

import pytest

from opticfilm_linux.batch import export as export_module
from opticfilm_linux.batch.export import (
    ExportProgress,
    cleanup_raw,
    export_batch,
    free_space,
    freeable_trays,
)
from opticfilm_linux.batch.model import (
    BatchDefaults,
    SlideRecord,
    SlideSettings,
    SlideStatus,
    TrayStage,
)
from opticfilm_linux.batch.review import accept_all, apply_slide_settings, set_slide_status
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.scanner.fake import FakeScanner
from opticfilm_linux.sweepstore import save_sweep
from synthetic import make_sweep

TIMEOUT = 120


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    """A batch with two trays already scanned and enhanced (both in review)."""
    sweep_dir = tmp_path_factory.mktemp("sweep600")
    save_sweep(make_sweep(dpi=600), sweep_dir)
    root = tmp_path_factory.mktemp("template") / "batch"
    store = BatchStore.create(root, name="T", defaults=BatchDefaults(dpi=600))
    runner = BatchRunner(store, FakeScanner(sweep_dir))
    runner.scan_tray()
    assert runner.wait_scans_idle(TIMEOUT)
    runner.scan_tray()
    assert runner.wait_idle(TIMEOUT)
    runner.close()
    store.close()
    return root


@pytest.fixture
def store(tmp_path, template):
    shutil.copytree(template, tmp_path / "b")
    with BatchStore.open(tmp_path / "b") as opened:
        yield opened


def test_export_reports_progress_per_slide(store, tmp_path):
    accept_all(store)
    calls: list[ExportProgress] = []
    result = export_batch(store, tmp_path / "out", on_progress=calls.append)
    assert calls[0] == ExportProgress(done=0, total=8)
    assert calls[-1] == ExportProgress(done=8, total=8)
    assert len(calls) == 9 and len(result.written) == 8 and not result.cancelled


def test_export_cancel_keeps_written_and_continues_later(store, tmp_path):
    accept_all(store)
    cancel = threading.Event()

    def stop_after_first(progress: ExportProgress) -> None:
        if progress.done == 1:
            cancel.set()

    first = export_batch(store, tmp_path / "out", on_progress=stop_after_first, cancel=cancel)
    assert first.cancelled and len(first.written) == 1
    exported = [s.exported for t in store.snapshot().trays for s in t.slides]
    assert exported.count(True) == 1
    second = export_batch(store, tmp_path / "out")
    assert len(second.written) == 7 and not second.cancelled
    assert len(list((tmp_path / "out").iterdir())) == 8


def test_export_to_unwritable_directory_raises_and_marks_nothing(store, tmp_path):
    accept_all(store)
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    with pytest.raises(OSError):
        export_batch(store, blocker)
    assert not any(s.exported for t in store.snapshot().trays for s in t.slides)
    assert [t.stage for t in store.snapshot().trays] == [TrayStage.REVIEW] * 2


def test_freeable_trays_lists_only_fully_exported(store, tmp_path):
    accept_all(store)
    assert freeable_trays(store) == ()
    export_batch(store, tmp_path / "out")
    found = freeable_trays(store)
    assert [f.number for f in found] == [1, 2] and all(f.bytes > 0 for f in found)
    cleanup_raw(store, 1)
    assert [f.number for f in freeable_trays(store)] == [2]


def test_free_space_returns_bytes_freed_and_reports_progress(store, tmp_path):
    accept_all(store)
    export_batch(store, tmp_path / "out")
    expected = sum(f.bytes for f in freeable_trays(store))
    calls: list[tuple[int, int]] = []
    freed = free_space(store, on_progress=lambda d, t: calls.append((d, t)))
    assert freed == expected and calls == [(1, 2), (2, 2)]
    assert all(t.raw_removed for t in store.snapshot().trays)


def test_free_space_cancel_stops_between_trays(store, tmp_path):
    accept_all(store)
    export_batch(store, tmp_path / "out")
    cancel = threading.Event()
    free_space(store, on_progress=lambda *_: cancel.set(), cancel=cancel)
    assert [t.raw_removed for t in store.snapshot().trays] == [True, False]
    assert store.snapshot().trays[1].slides[0].status is SlideStatus.ACCEPTED


def test_free_space_only_removes_the_listed_trays(store, tmp_path):
    accept_all(store)
    export_batch(store, tmp_path / "out")
    freed = free_space(store, only=(1,))
    assert [t.raw_removed for t in store.snapshot().trays] == [True, False]
    assert freed > 0


def test_export_stops_marking_a_tray_that_was_rescanned_meanwhile(store, tmp_path, monkeypatch):
    accept_all(store)
    real = export_module.write_slide
    calls: list[int] = []

    def rescan_after_first_write(*args, **kwargs):
        path = real(*args, **kwargs)
        if not calls:
            store.update_tray(1, sweep_version=2)  # what a re-scan does to the tray
        calls.append(1)
        return path

    monkeypatch.setattr(export_module, "write_slide", rescan_after_first_write)
    result = export_batch(store, tmp_path / "out")
    first, second = store.snapshot().trays
    assert not any(s.exported for s in first.slides)
    assert all(s.exported for s in second.slides)
    assert len(result.written) == 5


def test_export_refuses_a_destination_inside_the_batch(store):
    accept_all(store)
    inside = store.directory / "trays" / "0001" / "sweep-1" / "finished"
    with pytest.raises(BatchError, match="outside the batch"):
        export_batch(store, inside)
    assert not inside.exists()
    assert not any(s.exported for t in store.snapshot().trays for s in t.slides)


def test_editing_an_exported_slide_makes_it_exportable_again(store, tmp_path):
    accept_all(store)
    for position in (2, 3, 4):
        set_slide_status(store, 1, position, SlideStatus.NEEDS_LOOK)
    export_batch(store, tmp_path / "out")
    assert store.snapshot().trays[0].slides[0].exported
    apply_slide_settings(store, 1, 1, SlideSettings(rotation=90))
    assert not store.snapshot().trays[0].slides[0].exported
    result = export_batch(store, tmp_path / "out")
    assert len(result.written) == 1 and store.snapshot().trays[0].slides[0].exported


def test_export_does_not_mark_a_slide_edited_while_it_was_rendering(store, tmp_path, monkeypatch):
    accept_all(store)
    real = export_module.write_slide
    calls: list[int] = []

    def edit_after_first_write(*args, **kwargs):
        path = real(*args, **kwargs)
        if not calls:
            apply_slide_settings(store, 1, 1, SlideSettings(rotation=90))
        calls.append(1)
        return path

    monkeypatch.setattr(export_module, "write_slide", edit_after_first_write)
    export_batch(store, tmp_path / "out")
    slides = store.snapshot().trays[0].slides
    assert not slides[0].exported and all(s.exported for s in slides[1:])
    assert store.snapshot().trays[0].stage is TrayStage.REVIEW


def test_finishing_a_tray_rechecks_it_after_a_rescan(store, tmp_path, monkeypatch):
    accept_all(store)
    real = export_module._finish_tray_if_done

    def rescan_then_finish(store_, number):
        if number == 1:  # a re-scan completed after the last slide was exported
            fresh = tuple(
                SlideRecord(position=p, status=SlideStatus.ACCEPTED) for p in (1, 2, 3, 4)
            )
            store_.update_tray(1, sweep_version=2, slides=fresh)
        real(store_, number)

    monkeypatch.setattr(export_module, "_finish_tray_if_done", rescan_then_finish)
    export_batch(store, tmp_path / "out")
    first = store.snapshot().trays[0]
    assert first.stage is TrayStage.REVIEW and not any(s.exported for s in first.slides)
