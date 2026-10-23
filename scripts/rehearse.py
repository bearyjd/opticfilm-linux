"""Rehearse one real tray at a given resolution, without hardware, and measure what it costs.

    uv run python scripts/rehearse.py --dpi 3600 [--work DIR] [--keep]

The fake ``of135i`` driver (tests/fake_driver, FAKE_DRIVER_SYNTH=1) writes four synthetic
full-window frames in the real geometry at the requested dpi. Then the real code path runs:
DriverScanner.scan_holder -> save_sweep -> a BatchRunner tray scan + enhance -> export_batch
(16-bit TIFF). For each stage it prints wall time, peak memory of this process (anonymous memory,
i.e. excluding page cache and file mappings, and the whole RSS), the driver subprocess's peak
RSS, and the disk used. Big temporary data is deleted unless --keep. Not part of the test suite.
"""

import argparse
import os
import queue
import resource
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAKE_DRIVER = ROOT / "tests" / "fake_driver"
SAMPLE_S = 0.02
DISK_EVERY = 10  # walk the work folder every this many memory samples
GB = 1024**3
PEAK_PREFIX = "FAKE_DRIVER_PEAK_KIB="


@dataclass(frozen=True)
class StageResult:
    name: str
    seconds: float
    peak_anon: int  # bytes of anonymous memory (heap, numpy arrays): excludes the page cache
    peak_rss: int  # bytes of the whole resident set, file-backed mappings included
    driver_rss: int  # bytes; the largest driver subprocess so far (0 if none ran)
    disk_peak: int  # bytes; the most the work folder held during the stage (scratch included)
    work_disk: int  # bytes under the work folder when the stage ended (the batch included)
    batch_disk: int


def _status_kib(field: str) -> int:
    with open("/proc/self/status") as handle:
        for line in handle:
            if line.startswith(field + ":"):
                return int(line.split()[1])
    return 0


class PeakSampler:
    """Samples this process's memory in a background thread; reset between stages."""

    def __init__(self, disk: Path) -> None:
        self._disk = disk
        self._lock = threading.Lock()
        self._anon = self._rss = self._disk_peak = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> "PeakSampler":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()

    def _sample(self) -> None:
        anon, rss = _status_kib("RssAnon") * 1024, _status_kib("VmRSS") * 1024
        with self._lock:
            self._anon, self._rss = max(self._anon, anon), max(self._rss, rss)

    def _sample_disk(self) -> None:
        used = disk_used(self._disk)
        with self._lock:
            self._disk_peak = max(self._disk_peak, used)

    def _run(self) -> None:
        count = 0
        while not self._stop.wait(SAMPLE_S):
            self._sample()
            count += 1
            if count % DISK_EVERY == 0:
                self._sample_disk()

    def reset(self) -> None:
        with self._lock:
            self._anon = self._rss = self._disk_peak = 0
        self._sample()
        self._sample_disk()

    def peaks(self) -> tuple[int, int, int]:
        """Peak anonymous memory, peak RSS, and peak disk use of the work folder since reset."""
        self._sample()
        self._sample_disk()
        with self._lock:
            return self._anon, self._rss, self._disk_peak


def disk_used(path: Path) -> int:
    """Bytes allocated on disk under ``path`` (hard links counted once)."""
    if not path.exists():
        return 0
    seen: set[tuple[int, int]] = set()
    total = 0
    for item in [path, *path.rglob("*")]:
        try:
            info = item.lstat()
        except FileNotFoundError:  # a temporary file that went away meanwhile
            continue
        if (info.st_dev, info.st_ino) not in seen:
            seen.add((info.st_dev, info.st_ino))
            total += info.st_blocks * 512
    return total


def driver_peak(work: Path) -> int:
    """The largest peak RSS (VmHWM) the fake driver reported in its logs so far. (ru_maxrss of
    the children is no use here: it includes this process's pages, inherited at fork.)"""
    peaks = [0]
    for log in (work / "state").rglob("scan-*.log"):
        for line in log.read_text(errors="replace").splitlines():
            if line.startswith(PEAK_PREFIX):
                peaks.append(int(line.removeprefix(PEAK_PREFIX)) * 1024)
    return max(peaks)


class Rehearsal:
    def __init__(self, dpi: int, work: Path, sampler: PeakSampler) -> None:
        self.dpi, self.work, self.sampler = dpi, work, sampler
        self.batch_dir = work / "batch"
        self.results: list[StageResult] = []

    def stage(
        self, name: str, action: Callable[[], object], timer: Callable[[], float] | None = None
    ) -> object:
        """Run ``action`` and record it; ``timer`` overrides the wall time with its own figure."""
        self.sampler.reset()
        start = time.perf_counter()
        value = action()
        seconds = time.perf_counter() - start if timer is None else timer()
        anon, rss, disk = self.sampler.peaks()
        result = StageResult(
            name,
            seconds,
            anon,
            rss,
            driver_peak(self.work),
            disk,
            disk_used(self.work),
            disk_used(self.batch_dir),
        )
        self.results.append(result)
        print(_row(result), flush=True)
        return value


def _row(r: StageResult) -> str:
    return (
        f"{r.name:<22} {r.seconds:8.1f} s  anon {r.peak_anon / GB:6.2f} GB  "
        f"rss {r.peak_rss / GB:6.2f} GB  driver {r.driver_rss / GB:5.2f} GB  "
        f"disk peak {r.disk_peak / GB:6.2f} GB  work {r.work_disk / GB:6.2f} GB  "
        f"batch {r.batch_disk / GB:6.2f} GB"
    )


def _set_environment(work: Path) -> None:
    os.environ["OPTICFILM_DRIVER_DIR"] = str(FAKE_DRIVER)
    os.environ["FAKE_DRIVER_SYNTH"] = "1"
    os.environ["XDG_STATE_HOME"] = str(work / "state")
    os.environ["XDG_CACHE_HOME"] = str(work / "cache")


def _standalone(r: Rehearsal, scanner: object) -> None:
    """The backend and one slide on their own, outside the batch."""
    from opticfilm_linux.model import ScanSettings
    from opticfilm_linux.pipeline.run import ProcessOptions, enhance_slide
    from opticfilm_linux.pipeline.split import split_slides
    from opticfilm_linux.sweepstore import load_sweep, save_sweep

    sweep = r.stage("driver scan + stitch", lambda: scanner.scan_holder(ScanSettings(dpi=r.dpi)))
    r.stage("save_sweep", lambda: save_sweep(sweep, r.work / "sweep"))
    one = split_slides(load_sweep(r.work / "sweep"))[0]
    r.stage("enhance_slide (1)", lambda: enhance_slide(one, ProcessOptions(), r.dpi))
    r.stage("slide batch work (1)", lambda: _batch_work(one, r.dpi))


class EventClock:
    """Timestamps the runner's events as they arrive, so a background job's own start and end
    can be timed (enhancement starts before the scan job reports that it has finished)."""

    def __init__(self, events: "queue.Queue[object]") -> None:
        self._events = events
        self._seen: list[tuple[float, object]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                event = self._events.get(timeout=0.05)
            except queue.Empty:
                continue
            self._seen.append((time.perf_counter(), event))

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def first(self, wanted: object) -> float:
        time.sleep(0.2)  # let the drain thread catch up with events already queued
        for stamp, event in self._seen:
            if event == wanted:
                return stamp
        raise SystemExit(f"the runner never reported {wanted}")


def _batch(r: Rehearsal, scanner: object) -> None:
    """A tray through the batch engine: scan, background enhance, accept, export."""
    from opticfilm_linux.batch.events import TrayStageChanged
    from opticfilm_linux.batch.export import export_batch
    from opticfilm_linux.batch.model import BatchDefaults, TrayStage
    from opticfilm_linux.batch.runner import BatchRunner
    from opticfilm_linux.batch.store import BatchStore

    store = BatchStore.create(r.batch_dir, "Rehearsal", defaults=BatchDefaults(dpi=r.dpi))
    runner = BatchRunner(store, scanner)
    clock = EventClock(runner.events)
    try:
        r.stage("batch scan + save", lambda: (runner.scan_tray(), runner.wait_scans_idle()))

        def enhance_seconds() -> float:  # from the enhance job's start to the tray in review
            start = clock.first(TrayStageChanged(1, TrayStage.ENHANCING))
            return clock.first(TrayStageChanged(1, TrayStage.REVIEW)) - start

        r.stage("batch enhance (4)", runner.wait_idle, timer=enhance_seconds)
        tray = store.snapshot().trays[0]
        if tray.stage is not TrayStage.REVIEW:
            raise SystemExit(f"tray ended {tray.stage}: {tray.error}")
        runner.accept_all()
        version = tray.sweep_version
        r.stage("load + split (tray)", lambda: _load_split(store.sweep_dir(1, version)))
        r.stage("export tiff16 (4)", lambda: export_batch(store, r.work / "export", "tiff16"))
    finally:
        clock.stop()
        runner.close()
        store.close()


def run(dpi: int, work: Path) -> list[StageResult]:
    from opticfilm_linux.scanner.driver import DriverScanner

    _set_environment(work)
    with PeakSampler(work) as sampler:
        r = Rehearsal(dpi, work, sampler)
        scanner = DriverScanner()
        _standalone(r, scanner)  # its arrays are freed when it returns
        shutil.rmtree(work / "sweep")
        _batch(r, scanner)
    return r.results


def _load_split(directory: Path) -> object:
    """The per-tray cost the batch pays before any slide: map the sweep and find the slides."""
    from opticfilm_linux.pipeline.split import split_slides
    from opticfilm_linux.sweepstore import load_sweep

    return split_slides(load_sweep(directory))


def _batch_work(slide: object, dpi: int) -> None:
    """What the batch enhance worker does per slide, minus the manifest: blank check, enhance,
    two previews, flags. The tray's enhancement is judged against four times this."""
    from opticfilm_linux.batch.enhance import BEFORE_GAMMA
    from opticfilm_linux.batch.flags import compute_flags
    from opticfilm_linux.batch.previews import make_preview
    from opticfilm_linux.pipeline.run import ProcessOptions, enhance_slide
    from opticfilm_linux.pipeline.split import is_blank

    is_blank(slide)
    enhanced = enhance_slide(slide, ProcessOptions(), dpi)
    make_preview(slide.rgb, gamma=BEFORE_GAMMA)
    make_preview(enhanced)
    compute_flags(enhanced, slide.ir, dpi)


def print_table(dpi: int, results: list[StageResult]) -> None:
    print(f"\n== {dpi} dpi ==")
    for result in results:
        print(_row(result))
    total = sum(r.seconds for r in results)
    print(
        f"{'TOTAL':<22} {total:8.1f} s  peak anon {max(r.peak_anon for r in results) / GB:.2f} GB"
        f"  peak rss {max(r.peak_rss for r in results) / GB:.2f} GB"
        f"  (ru_maxrss {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**2:.2f} GB)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dpi", type=int, default=3600)
    parser.add_argument("--work", type=Path, help="scratch folder (default: in ~/.cache)")
    parser.add_argument("--keep", action="store_true", help="keep the sweep, batch and exports")
    args = parser.parse_args(argv)
    base = args.work or Path.home() / ".cache" / "opticfilm-linux" / "rehearsal"
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"rehearse-{args.dpi}-", dir=base))
    print(f"rehearsing a {args.dpi} dpi tray in {work}", flush=True)
    try:
        print_table(args.dpi, run(args.dpi, work))
    finally:
        if args.keep:
            print(f"kept {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
