import dataclasses
import shutil

import numpy as np
import pytest
import tifffile

from opticfilm_linux.batch.enhance import options_for
from opticfilm_linux.batch.export import CleanupRefused, cleanup_raw, export_batch
from opticfilm_linux.batch.model import BatchDefaults, SlideSettings, SlideStatus, TrayStage
from opticfilm_linux.batch.review import accept_all, apply_slide_settings
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.store import BatchStore
from opticfilm_linux.pipeline.run import enhance_slide
from opticfilm_linux.pipeline.split import split_slides
from opticfilm_linux.scanner.fake import FakeScanner
from opticfilm_linux.sweepstore import load_sweep, save_sweep
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
    return BatchStore.open(tmp_path / "b")


def _mark(store, number, position, **changes):
    def change(manifest):
        trays = []
        for tray in manifest.trays:
            if tray.number == number:
                slides = tuple(
                    dataclasses.replace(s, **changes) if s.position == position else s
                    for s in tray.slides
                )
                tray = dataclasses.replace(tray, slides=slides)
            trays.append(tray)
        return dataclasses.replace(manifest, trays=tuple(trays))

    store.update(change)


def _files(directory):
    return sorted(p.name for p in directory.iterdir())


def test_export_writes_only_accepted_slides(store, tmp_path):
    _mark(store, 1, 3, status=SlideStatus.NEEDS_LOOK)
    result = export_batch(store, tmp_path / "out")
    assert len(result.written) == 7
    assert store.snapshot().trays[0].stage is TrayStage.REVIEW
    assert store.snapshot().trays[1].stage is TrayStage.EXPORTED


def test_export_is_recomputed_from_raw_and_settings(store, tmp_path):
    settings = SlideSettings(brightness_ev=1.0, rotation=90)
    apply_slide_settings(store, 1, 1, settings)
    result = export_batch(store, tmp_path / "out")
    sweep = load_sweep(store.sweep_dir(1, 1))
    options = options_for(store.snapshot().defaults, "positive", settings)
    expected = enhance_slide(split_slides(sweep)[0], options, sweep.dpi)
    assert np.array_equal(tifffile.imread(result.written[0]), expected)


def test_second_export_writes_nothing_new(store, tmp_path):
    export_batch(store, tmp_path / "out")
    names = _files(tmp_path / "out")
    again = export_batch(store, tmp_path / "out")
    assert again.written == () and _files(tmp_path / "out") == names and len(names) == 8


def test_exported_trays_become_exported_when_everything_is_done(store, tmp_path):
    accept_all(store)
    export_batch(store, tmp_path / "out")
    assert [t.stage for t in store.snapshot().trays] == [TrayStage.EXPORTED] * 2
    assert all(s.exported for t in store.snapshot().trays for s in t.slides)


def test_export_never_overwrites_existing_files(store, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "0001.tif").write_bytes(b"keep me")
    export_batch(store, out)
    assert (out / "0001.tif").read_bytes() == b"keep me" and len(_files(out)) == 9


def test_blank_slides_are_never_exported(store, tmp_path):
    _mark(store, 1, 2, status=SlideStatus.BLANK)
    assert len(export_batch(store, tmp_path / "out").written) == 7


def _export_everything(store, tmp_path):
    accept_all(store)
    export_batch(store, tmp_path / "out")


def test_cleanup_refused_for_a_tray_in_review(store):
    with pytest.raises(CleanupRefused):
        cleanup_raw(store, 1)
    assert store.sweep_dir(1, 1).is_dir()


def test_cleanup_frees_bytes_and_marks_raw_removed(store, tmp_path):
    _export_everything(store, tmp_path)
    freed = cleanup_raw(store, 1)
    assert freed > 0 and not store.sweep_dir(1, 1).exists()
    assert store.snapshot().trays[0].raw_removed
    assert store.preview_path(1, 1, "after").is_file()
    assert (store.directory / "batch.json").is_file()
    assert store.sweep_dir(2, 1).is_dir()


def test_cleanup_removes_all_sweep_versions(store, tmp_path):
    shutil.copytree(store.sweep_dir(1, 1), store.sweep_dir(1, 2))
    _export_everything(store, tmp_path)
    cleanup_raw(store, 1)
    assert not store.sweep_dir(1, 1).exists() and not store.sweep_dir(1, 2).exists()


def test_cleanup_refuses_a_symlinked_sweep(store, tmp_path):
    _export_everything(store, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("do not delete")
    shutil.rmtree(store.sweep_dir(1, 1))
    store.sweep_dir(1, 1).symlink_to(outside, target_is_directory=True)
    with pytest.raises(CleanupRefused, match="symlink"):
        cleanup_raw(store, 1)
    assert (outside / "precious.txt").read_text() == "do not delete"
    assert not store.snapshot().trays[0].raw_removed
