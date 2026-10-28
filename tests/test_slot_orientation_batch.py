"""Per-slot default orientation through a whole batch, the CLI and the app (fake scanner)."""

import numpy as np
import pytest
import tifffile
from PIL import Image

from controller_helpers import make_controller, wait_until
from opticfilm_linux.batch.export import export_batch
from opticfilm_linux.batch.model import BatchDefaults, SlideSettings, SlotOrientation
from opticfilm_linux.batch.review import accept_all, apply_slide_settings, set_batch_orientation
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.store import BatchStore
from opticfilm_linux.cli import main
from opticfilm_linux.scanner.fake import FakeScanner

TIMEOUT = 120
SLOTS = (SlotOrientation(2, 90), SlotOrientation(3, 180, True))


def _scan(store, sweep_dir, trays=1):
    runner = BatchRunner(store, FakeScanner(sweep_dir))
    try:
        for _ in range(trays):
            runner.scan_tray()
            assert runner.wait_scans_idle(TIMEOUT)
        assert runner.wait_idle(TIMEOUT)
    finally:
        runner.close()


def _create(path, slots=SLOTS, **defaults):
    return BatchStore.create(
        path, name="T", defaults=BatchDefaults(dpi=600, slots=slots, **defaults)
    )


def _slide(store, number, position):
    tray = next(t for t in store.snapshot().trays if t.number == number)
    return next(s for s in tray.slides if s.position == position)


def _size(store, number, position):
    with Image.open(store.preview_path(number, position, "after")) as image:
        return image.size


def _exported(store, directory):
    accept_all(store)
    return [tifffile.imread(p) for p in export_batch(store, directory).written]


@pytest.fixture(scope="module")
def plain_export(tmp_path_factory, sweep600_dir):
    """The four slides of one tray exported from a batch without slot orientations."""
    root = tmp_path_factory.mktemp("plain")
    with _create(root / "b", slots=()) as store:
        _scan(store, sweep600_dir)
        return _exported(store, root / "out")


def test_every_new_tray_gets_the_slot_orientation_without_it_being_an_owner_choice(
    tmp_path, sweep600_dir
):
    with _create(tmp_path / "b") as store:
        _scan(store, sweep600_dir, trays=2)
        for number in (1, 2):
            width, height = _size(store, number, 1)
            assert _size(store, number, 2) == (height, width)  # turned a quarter
            assert _size(store, number, 3) == (width, height)
            for position in range(1, 5):
                slide = _slide(store, number, position)
                assert slide.settings == SlideSettings() and not slide.owner_decided


def test_export_recomputes_each_slot_with_its_orientation(tmp_path, sweep600_dir, plain_export):
    with _create(tmp_path / "b") as store:
        _scan(store, sweep600_dir)
        turned = _exported(store, tmp_path / "out")
    assert np.array_equal(turned[0], plain_export[0])
    assert np.array_equal(turned[1], np.rot90(plain_export[1], k=-1))
    assert np.array_equal(turned[2], np.fliplr(np.rot90(plain_export[2], k=2)))
    assert np.array_equal(turned[3], plain_export[3])


def test_a_slides_own_rotation_applies_on_top_and_is_never_overwritten(
    tmp_path, sweep600_dir, plain_export
):
    with _create(tmp_path / "b") as store:
        _scan(store, sweep600_dir)
        apply_slide_settings(store, 1, 2, SlideSettings(rotation=90))
        _scan(store, sweep600_dir)  # another tray arrives and is enhanced afterwards
        owned = _slide(store, 1, 2)
        assert owned.settings == SlideSettings(rotation=90) and owned.owner_decided
        assert _slide(store, 2, 2).settings == SlideSettings()
        turned = _exported(store, tmp_path / "out")
    assert np.array_equal(turned[1], np.rot90(plain_export[1], k=2))  # slot 90 + own 90
    assert np.array_equal(turned[5], np.rot90(plain_export[1], k=-1))  # tray 2: slot only


def test_resetting_a_slide_goes_back_to_its_slot_orientation(tmp_path, sweep600_dir, plain_export):
    with _create(tmp_path / "b") as store:
        _scan(store, sweep600_dir)
        apply_slide_settings(store, 1, 2, SlideSettings(rotation=270))
        apply_slide_settings(store, 1, 2, SlideSettings())
        turned = _exported(store, tmp_path / "out")
    assert np.array_equal(turned[1], np.rot90(plain_export[1], k=-1))


def test_slot_orientations_survive_closing_and_reopening_the_batch(
    tmp_path, sweep600_dir, plain_export
):
    _create(tmp_path / "b").close()
    with BatchStore.open(tmp_path / "b") as store:
        assert store.snapshot().defaults.slots == SLOTS
        _scan(store, sweep600_dir)
    with BatchStore.open(tmp_path / "b") as store:
        turned = _exported(store, tmp_path / "out")
    assert np.array_equal(turned[1], np.rot90(plain_export[1], k=-1))


def test_changing_the_batch_orientation_keeps_the_slot_orientations(tmp_path, sweep600_dir):
    with _create(tmp_path / "b") as store:
        _scan(store, sweep600_dir)
        set_batch_orientation(store, 180, True)
        assert store.snapshot().defaults.slots == SLOTS


# -- CLI -------------------------------------------------------------------------------------


def _new(path, *extra):
    return main(["batch", "new", str(path), "--name", "Trips", "--dpi", "600", *extra])


def test_batch_new_stores_slot_orientations(tmp_path):
    assert _new(tmp_path / "b", "--slot-rotate", "3=180, 2=90", "--slot-mirror", "3") == 0
    with BatchStore.open(tmp_path / "b", readonly=True) as store:
        assert store.snapshot().defaults.slots == SLOTS


def test_batch_new_without_slot_options_stores_none(tmp_path):
    assert _new(tmp_path / "b") == 0
    with BatchStore.open(tmp_path / "b", readonly=True) as store:
        assert store.snapshot().defaults.slots == ()


@pytest.mark.parametrize(
    "option, value, message",
    [
        ("--slot-rotate", "2", "SLOT=DEGREES"),
        ("--slot-rotate", "two=90", "SLOT=DEGREES"),
        ("--slot-rotate", "2=45", "0, 90, 180 or 270"),
        ("--slot-rotate", "2=ninety", "0, 90, 180 or 270"),
        ("--slot-rotate", "0=90", "slot numbers start at 1"),
        ("--slot-rotate", "2=90,2=180", "slot 2 is given twice"),
        ("--slot-rotate", "", "SLOT=DEGREES"),
        ("--slot-mirror", "0", "slot numbers start at 1"),
        ("--slot-mirror", "x", "slot numbers"),
        ("--slot-mirror", "1,1", "slot 1 is given twice"),
    ],
)
def test_batch_new_rejects_bad_slot_options_with_a_clear_message(
    tmp_path, capsys, option, value, message
):
    with pytest.raises(SystemExit) as excinfo:
        _new(tmp_path / "b", option, value)
    assert excinfo.value.code == 2
    error = capsys.readouterr().err.splitlines()[-1]  # not the usage line above it
    assert error.startswith("ofscan batch new: error:")
    assert option in error and message in error
    assert not (tmp_path / "b").exists()


# -- app -------------------------------------------------------------------------------------


def test_the_app_creates_a_batch_with_slot_orientations(tmp_path, sweep600_dir):
    c = make_controller(sweep600_dir)
    try:
        c.new_batch(tmp_path / "b", "Test", "", None, 600, slots=SLOTS)
        wait_until(c, lambda: c.state().batch_name == "Test")
    finally:
        c.shutdown()
    with BatchStore.open(tmp_path / "b", readonly=True) as store:
        assert store.snapshot().defaults.slots == SLOTS
