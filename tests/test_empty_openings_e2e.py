"""A tray with empty holder openings goes scan -> auto-accept -> export -> free, on the fake
scanner through the real runner, enhancement and AutoPipeline (no mocked sweeps)."""

import dataclasses
from pathlib import Path

import pytest

from opticfilm_linux.app.auto import AutoPipeline
from opticfilm_linux.batch.empty import empty_frame_fingerprints
from opticfilm_linux.batch.model import BatchDefaults, SlideStatus, TrayStage
from opticfilm_linux.batch.review import confirm_empty_frames
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.store import BatchStore
from opticfilm_linux.scanner.fake import FakeScanner
from opticfilm_linux.scanner.positions import slides_arg
from opticfilm_linux.sweepstore import save_sweep
from synthetic import make_sweep

TIMEOUT = 240
FIRST_TRAY = ["001.jpg", "002.jpg", "003.jpg", "004.jpg"]


class Rig:
    """One collection batch; each ``scan`` replays its own sweep, like a fresh holder."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        defaults = BatchDefaults(dpi=600, auto_export=True, export_dir=str(tmp_path / "out"))
        self.store = BatchStore.create(
            tmp_path / "batch", name="Box", defaults=defaults, collection="family"
        )
        self.auto = AutoPipeline(self.store)
        self.scanners: list[FakeScanner] = []

    def scan(self, filled, skipped=()) -> int:
        """``filled`` has one entry per scanned window (a False one is a bare opening)."""
        scanned = tuple(p for p in (1, 2, 3, 4) if p not in skipped)
        sweep = dataclasses.replace(make_sweep(dpi=600, filled=filled), positions=scanned)
        folder = self.tmp / f"sweep{len(self.scanners)}"
        save_sweep(sweep, folder)
        scanner = FakeScanner(folder)
        self.scanners.append(scanner)
        runner = BatchRunner(self.store, scanner)
        number = runner.scan_tray(skipped)
        assert runner.wait_idle(TIMEOUT)
        runner.close()
        return number

    def tray(self, number: int):
        return next(t for t in self.store.snapshot().trays if t.number == number)

    def run(self, number: int) -> None:
        assert self.auto.arm(number)
        self.auto.run(number)

    def release_first_tray(self) -> None:
        number = self.scan((True,) * 4)
        self.run(number)
        self.auto.confirm_free_held(number)
        assert self.tray(number).auto == "freed"

    def album(self) -> list[str]:
        folder = self.tmp / "out" / "family" / "box-1" / "a"
        return sorted(p.name for p in folder.glob("*.jpg"))


@pytest.fixture
def rig(tmp_path):
    rig = Rig(tmp_path)
    yield rig
    rig.store.close()


def _assert_clean_finish(tray) -> None:
    assert tray.stage is TrayStage.EXPORTED
    assert tray.auto == "freed" and tray.raw_removed
    assert not tray.auto_error and not tray.auto_failed and not tray.error


@pytest.mark.parametrize(
    ("skipped", "arg", "files"),
    [
        ((4,), "1-3", ["005.jpg", "006.jpg", "007.jpg"]),
        ((2,), "1,3,4", ["005.jpg", "007.jpg", "008.jpg"]),
        ((3, 4), "1-2", ["005.jpg", "006.jpg"]),
        ((2, 3, 4), "1", ["005.jpg"]),
        ((1, 4), "2-3", ["006.jpg", "007.jpg"]),
    ],
)
def test_declared_empty_openings_are_not_scanned_and_leave_gaps(rig, skipped, arg, files):
    rig.release_first_tray()
    number = rig.scan((True,) * (4 - len(skipped)), skipped)
    rig.run(number)
    tray = rig.tray(number)
    _assert_clean_finish(tray)
    requests = rig.scanners[-1].scan_requests
    assert len(requests) == 1 and slides_arg(requests[0].positions) == arg
    assert {s.position for s in tray.slides if s.status is SlideStatus.SKIPPED} == set(skipped)
    assert rig.album() == FIRST_TRAY + files


def test_declared_empty_opening_on_the_first_tray_waits_once_for_release_then_frees(rig):
    number = rig.scan((True,) * 3, (4,))
    rig.run(number)
    assert rig.tray(number).auto == "held" and not rig.tray(number).raw_removed
    assert rig.album() == ["001.jpg", "002.jpg", "003.jpg"]
    rig.auto.confirm_free_held(number)
    _assert_clean_finish(rig.tray(number))


def test_empty_opening_found_after_the_scan_keeps_its_number_and_frees_once_confirmed(rig):
    rig.release_first_tray()
    number = rig.scan((True, False, True, True))  # opening 2 is a bare, clear window
    tray = rig.tray(number)
    assert tray.stage is TrayStage.REVIEW
    assert tray.slides[1].status is SlideStatus.BLANK
    rig.run(number)
    tray = rig.tray(number)
    # The raw of an unconfirmed empty frame is never freed on its own: one owner confirmation.
    assert tray.auto == "held" and not tray.raw_removed and "look empty" in tray.auto_error
    assert not tray.auto_failed and tray.stage is TrayStage.EXPORTED
    assert rig.album() == FIRST_TRAY + ["005.jpg", "007.jpg", "008.jpg"]
    confirm_empty_frames(
        rig.store,
        number,
        (2,),
        expected_fingerprints=empty_frame_fingerprints(tray),
        expected_sweep_version=tray.sweep_version,
    )
    rig.auto.run(number)
    _assert_clean_finish(rig.tray(number))
    assert rig.album() == FIRST_TRAY + ["005.jpg", "007.jpg", "008.jpg"]
    assert len(rig.scanners[-1].scan_requests) == 1
