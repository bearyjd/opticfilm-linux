"""The real scanner, through the community driver's command line (``python -m of135i``).

The OpticFilm 135i scans a mounted-slide holder one opening at a time: ``scan --frames 1-4``
loads nothing and calibrates once, then writes one full-window frame per opening, with the
slide's long side along the transport. This backend runs that command, turns each frame a
quarter turn, and stacks the four into one sweep, so the splitting, enhancement and batch code
work unchanged. It sends nothing to the scanner itself: every hardware command is the driver's.

The driver must already have *loaded* the holder (an interactive step: ``of135i load``).
``status()`` reads the driver's read-only ``doctor`` report to tell whether that happened.
"""

import dataclasses
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import tifffile

from opticfilm_linux.model import HolderKind, RawSweep, ScanSettings
from opticfilm_linux.scanner.base import ScanError, ScannerStatus

log = logging.getLogger(__name__)

FRAMES = "1-4"
FRAME_QUARTER_TURNS = 1  # counter-clockwise; turns a frame so the slide's long side lies along x
DRIVER_ENV = "OPTICFILM_DRIVER_DIR"
SCAN_TIMEOUT_S = 3600
STATUS_TIMEOUT_S = 20
STATUS_MAX_AGE_S = 3.0  # the status poll must stay cheap: each answer costs a process start
LOG_TAIL_CHARS = 1500
IDLE_HOMED = 0x22
LOADED_STATUS_HIGH_BYTE = 0xDC  # status word 0xdc55 after a completed load (hardware-observed)
POWER_CYCLE_HINT = (
    "The scanner refused or lost the scan. Power-cycle it (off until the lights go out, then on), "
    "eject, load the holder again and retry."
)


@dataclasses.dataclass(frozen=True)
class DriverConfig:
    driver_dir: Path | None = None  # a checkout holding the ``of135i`` package; None: importable
    python: str = sys.executable
    log_dir: Path | None = None
    work_dir: Path | None = None  # scratch for the scan; a disk, never a RAM-backed /tmp
    timeout_s: int = SCAN_TIMEOUT_S

    @classmethod
    def from_env(cls) -> "DriverConfig":
        directory = os.environ.get(DRIVER_ENV)
        state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
        cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        return cls(
            driver_dir=Path(directory) if directory else None,
            log_dir=state / "opticfilm-linux" / "logs",
            work_dir=cache / "opticfilm-linux" / "work",
        )

    def available(self) -> bool:
        if self.driver_dir is not None:
            return (self.driver_dir / "of135i").is_dir()
        probe = subprocess.run(
            [self.python, "-c", "import of135i"], capture_output=True, check=False
        )
        return probe.returncode == 0


def parse_doctor(text: str) -> ScannerStatus:
    """Map the driver's read-only ``doctor`` report to a scanner status.

    Ready means: homed and idle (reg 0x01 = 0x22), the holder present, AND the loaded status
    class (high byte 0xdc, the word the vendor's load ends on). Homed and idle with the holder
    merely present is also what the scanner reports right after an eject, so it is not enough.
    """
    state = re.search(r"reg 0x01 = 0x([0-9a-fA-F]{2})", text)
    if state is None:
        return ScannerStatus.NOT_CONNECTED
    code = int(state.group(1), 16)
    if code == 0x00:  # freshly powered: the holder has not been loaded yet
        return ScannerStatus.NOT_READY
    if code != IDLE_HOMED:
        return ScannerStatus.ERROR  # an engine state the driver refuses to start from
    present = re.search(r"present:\s*(True|False)", text)
    word = re.search(r"status word:\s*0x([0-9a-fA-F]{4})", text)
    if not (present and present.group(1) == "True" and word):
        return ScannerStatus.NOT_READY
    loaded = int(word.group(1), 16) >> 8 == LOADED_STATUS_HIGH_BYTE
    return ScannerStatus.READY if loaded else ScannerStatus.NOT_READY


class DriverScanner:
    def __init__(self, config: DriverConfig | None = None) -> None:
        self._config = config or DriverConfig.from_env()
        self._scanning = threading.Event()
        self._status_lock = threading.Lock()
        self._hw_lock = threading.Lock()  # the driver allows one process on the scanner at a time
        self._status_cache: tuple[float, ScannerStatus] | None = None

    # -- Scanner protocol ----------------------------------------------------------------

    def scan_holder(self, settings: ScanSettings) -> RawSweep:
        self._require_driver()
        self._scanning.set()  # status() answers BUSY from now on, without touching the scanner
        try:
            with self._hw_lock:  # waits for a status check already in progress
                return self._scan_locked(settings)
        finally:
            self._scanning.clear()
            self._status_cache = None

    def _scan_locked(self, settings: ScanSettings) -> RawSweep:
        base = self._config.work_dir
        if base is not None:
            base.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="opticfilm-scan-", dir=base))
        try:
            output = work / "tray.tiff"
            command = ["scan", "--frames", FRAMES, "--dpi", str(settings.dpi), "--ir"]
            command += ["-o", str(output)]
            result = self._run(command, self._config.timeout_s, keep_log=True)
            self._keep_sidecars(work, failed=_missing_frames(work))
            return _stitch(work, settings.dpi, result)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _keep_sidecars(self, work: Path, failed: bool) -> None:
        """Keep the driver's calibration sidecars beside the log: they explain a bad scan."""
        directory = self._config.log_dir
        if directory is None:
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        patterns = ["*.diag.json", "*.cal.npz"] if failed else ["*.diag.json"]
        try:
            directory.mkdir(parents=True, exist_ok=True)
            for pattern in patterns:
                for path in work.glob(pattern):
                    shutil.copy2(path, directory / f"{stamp}-{path.name}")
        except OSError:
            log.warning("could not keep the driver's sidecar files in %s", directory)

    def status(self) -> ScannerStatus:
        if self._scanning.is_set():
            return ScannerStatus.BUSY
        with self._status_lock:
            cached = self._status_cache
            if cached is not None and time.monotonic() - cached[0] < STATUS_MAX_AGE_S:
                return cached[1]
            if not self._hw_lock.acquire(blocking=False):
                return ScannerStatus.BUSY  # a scan or eject holds the driver
            try:
                status = self._read_status()
            finally:
                self._hw_lock.release()
            self._status_cache = (time.monotonic(), status)
            return status

    def eject(self) -> None:
        self._require_driver()
        with self._hw_lock:
            result = self._run(["eject"], STATUS_TIMEOUT_S * 6, keep_log=True)
            self._status_cache = None
        if result.returncode != 0:
            raise ScanError(f"eject failed: {_tail(result.output)}")

    # -- internals -----------------------------------------------------------------------

    def _require_driver(self) -> None:
        if not self._config.available():
            where = self._config.driver_dir or "the Python path"
            raise ScanError(
                f"the scanner driver (of135i) was not found in {where}; "
                f"set {DRIVER_ENV} to a checkout of opticfilm135i-linux"
            )

    def _read_status(self) -> ScannerStatus:
        try:
            self._require_driver()
            result = self._run(["doctor"], STATUS_TIMEOUT_S, keep_log=False)
        except ScanError:
            return ScannerStatus.ERROR
        except subprocess.TimeoutExpired:
            return ScannerStatus.ERROR
        return parse_doctor(result.output)

    def _run(self, args: Sequence[str], timeout: int, keep_log: bool) -> "_Result":
        env = dict(os.environ)
        if self._config.driver_dir is not None:
            extra = str(self._config.driver_dir)
            env["PYTHONPATH"] = extra + os.pathsep + env.get("PYTHONPATH", "")
        started = time.strftime("%Y%m%d-%H%M%S")
        try:
            done = subprocess.run(
                [self._config.python, "-m", "of135i", *args],
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ScanError(
                f"the driver did not finish within {timeout} s. {POWER_CYCLE_HINT}"
            ) from exc
        output = (done.stdout or "") + (done.stderr or "")
        if keep_log:
            self._write_log(f"{args[0]}-{started}", output)
        return _Result(done.returncode, output)

    def _write_log(self, name: str, output: str) -> None:
        directory = self._config.log_dir
        if directory is None:
            return
        try:
            directory.mkdir(parents=True, exist_ok=True)
            (
                directory
                / f"{name.replace('scan-', 'scan-', 1) if name.startswith('scan') else name}.log"
            ).write_text(output)
        except OSError:
            log.warning("could not write the driver log to %s", directory)


@dataclasses.dataclass(frozen=True)
class _Result:
    returncode: int
    output: str


def _tail(text: str) -> str:
    text = text.strip()
    return text[-LOG_TAIL_CHARS:]


def _frame_files(work: Path, number: int) -> tuple[Path, Path]:
    return work / f"tray-f{number}.overscan.tiff", work / f"tray-f{number}-ir.overscan.tiff"


def _missing_frames(work: Path) -> bool:
    return any(not p.is_file() for n in range(1, 5) for p in _frame_files(work, n))


def _read_ir(path: Path) -> np.ndarray:
    plane = tifffile.imread(path)
    if plane.ndim == 3:  # the driver stores the infrared plane in three equal channels
        plane = plane.mean(axis=2)
    return np.asarray(plane).astype(np.uint16)


def _turned_shape(path: Path) -> tuple[int, int]:
    """(rows, columns) of a frame after the quarter turn, read without loading the pixels."""
    with tifffile.TiffFile(path) as handle:
        height, width = handle.pages[0].shape[:2]
    return (width, height) if FRAME_QUARTER_TURNS % 2 else (height, width)


def _stitch(work: Path, dpi: int, result: _Result) -> RawSweep:
    if _missing_frames(work):
        raise ScanError(f"{POWER_CYCLE_HINT} Driver said: {_tail(result.output)}")
    paths = [_frame_files(work, n) for n in range(1, 5)]
    shapes = [_turned_shape(rgb_path) for rgb_path, _ in paths]
    rows, width = sum(h for h, _ in shapes), max(w for _, w in shapes)
    # The sweep lives in files beside the scratch folder, one frame in memory at a time: a
    # 7200 dpi tray is several gigabytes. The files are unlinked once mapped.
    stack = Path(tempfile.mkdtemp(prefix="opticfilm-sweep-", dir=work.parent))
    try:
        rgb_all = np.lib.format.open_memmap(
            stack / "rgb.npy", mode="w+", dtype=np.uint16, shape=(rows, width, 3)
        )
        ir_all = np.lib.format.open_memmap(
            stack / "ir.npy", mode="w+", dtype=np.uint16, shape=(rows, width)
        )
        row = 0
        for (rgb_path, ir_path), (height, frame_width) in zip(paths, shapes, strict=True):
            rgb_all[row : row + height, :frame_width] = np.rot90(
                tifffile.imread(rgb_path), FRAME_QUARTER_TURNS
            )
            ir_all[row : row + height, :frame_width] = np.rot90(
                _read_ir(ir_path), FRAME_QUARTER_TURNS
            )
            row += height
        return RawSweep(rgb=rgb_all, ir=ir_all, dpi=dpi, holder=HolderKind.SLIDE4)
    finally:
        shutil.rmtree(stack, ignore_errors=True)  # the mappings stay valid after unlinking
