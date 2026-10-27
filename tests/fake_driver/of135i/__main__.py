"""A stand-in for the community driver's command line, for tests (no hardware).

``scan`` writes the files the real driver writes for a mounted-slide holder: one full-window
frame per opening, with the slide's long side along the transport (so a frame is a slide turned
a quarter turn, with dark holder on both sides). Behaviour is chosen with FAKE_DRIVER_MODE:
ok | coverage_fail (overscan files only, exit 1, as for a slide the strip detector rejects) |
cal_fail (the real driver's calibration refusal, no files) | slow (ok after a pause).
``load --next-strip`` exits with FAKE_DRIVER_LOAD_EXIT after FAKE_DRIVER_LOAD_DELAY seconds.
FAKE_DRIVER_SYNTH=1 makes the frames up instead (``synth``: the real frame geometry at the
requested --dpi, written strip by strip) rather than cutting them from FAKE_DRIVER_SWEEP.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import tifffile

from opticfilm_linux.pipeline.split import split_slides
from opticfilm_linux.sweepstore import load_sweep

from . import synth

PAD = 40  # dark holder either side of the opening, in pixels
CAL_FAIL = (
    "ERROR of135i: hardware session FAILED in operation scan, phase cal_dark_b: CalibrationError: "
    "dark_b is residual and no healthy dark_b has been measured this session. "
    "Power the scanner OFF, wait until its lights go out, then power it ON again."
)
_STATE = (
    "== State ==\n  reg 0x01 = 0x{reg}\n  status word: 0x{word}\n"
    "== Magazine ==\n  present: {present}\n"
)
DOCTOR = {
    "idle": _STATE.format(reg="22 (idle-homed)", word="dc55", present="True"),  # loaded
    "unloaded": _STATE.format(reg="22 (idle-homed)", word="f855", present="True"),  # after eject
    "cold": _STATE.format(reg="00 (cold-never-homed)", word="4855", present="True"),
    "absent": _STATE.format(reg="22 (idle-homed)", word="dc55", present="False"),
}


def _frames(sweep_dir: Path):
    for slide in split_slides(load_sweep(sweep_dir)):
        rgb = np.pad(slide.rgb, ((PAD, PAD), (0, 0), (0, 0)))
        ir = np.pad(slide.ir, ((PAD, PAD), (0, 0)))
        # the real frame has the transport along its rows: turn the slide a quarter turn
        yield np.rot90(rgb, k=-1).copy(), np.rot90(ir, k=-1).copy()


def _trace(event: str) -> None:
    """Append a timestamped event, so tests can see whether two commands overlapped."""
    path = os.environ.get("FAKE_DRIVER_TRACE")
    if path:
        with open(path, "a") as handle:
            handle.write(f"{time.monotonic():.4f} {event}\n")


def _scan(args: argparse.Namespace) -> int:
    _trace("scan start")
    print(
        f"INFO of135i: opened device bus=3 addr={os.environ.get('FAKE_DRIVER_ADDR', '18')}",
        flush=True,
    )
    try:
        return _scan_body(args)
    finally:
        _trace("scan end")


def _scan_body(args: argparse.Namespace) -> int:
    mode = os.environ.get("FAKE_DRIVER_MODE", "ok")
    argv_log = os.environ.get("FAKE_DRIVER_ARGV")
    if argv_log:
        Path(argv_log).write_text(json.dumps(sys.argv[1:]))
    if mode == "cal_fail":
        print(CAL_FAIL, file=sys.stderr)
        return 1
    if mode == "hang":  # a driver stuck mid-scan, with a helper child of its own
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        pids = os.environ.get("FAKE_DRIVER_PIDS")
        if pids:
            Path(pids).write_text(json.dumps([os.getpid(), child.pid]))
        print("scanning frame 1 ...", flush=True)
        try:
            time.sleep(120)
        except KeyboardInterrupt:  # as the real driver: Ctrl-C fails closed and exits by itself
            print("ERROR of135i: interrupted; failing closed", flush=True)
            return int(os.environ.get("FAKE_DRIVER_SIGINT_EXIT", "130"))  # 130, as the real one
    if mode == "noisy":  # bytes that are not valid text, and a lot of them
        sys.stdout.buffer.write(b"\xff\xfe not text \x80\n")
        sys.stdout.buffer.write(b"poll timed out\n" * 400_000)
        sys.stdout.flush()
    if mode == "slow":
        time.sleep(float(os.environ.get("FAKE_DRIVER_DELAY", "1.5")))
    out = Path(args.output)
    if os.environ.get("FAKE_DRIVER_SYNTH") == "1":
        return _write_synthetic(out, args.dpi, mode)
    for number, (rgb, ir) in enumerate(_frames(Path(os.environ["FAKE_DRIVER_SWEEP"])), start=1):
        stem = out.with_name(f"{out.stem}-f{number}")
        tifffile.imwrite(f"{stem}.overscan.tiff", rgb)
        tifffile.imwrite(f"{stem}-ir.overscan.tiff", np.stack([ir, ir, ir], axis=2))
        Path(f"{stem}.diag.json").write_text(json.dumps({"frame": number}))
        if mode != "coverage_fail":
            tifffile.imwrite(f"{stem}.tiff", rgb)
    return 1 if mode == "coverage_fail" else 0


def _write_synthetic(out: Path, dpi: int, mode: str) -> int:
    for number in range(1, 5):
        stem = out.with_name(f"{out.stem}-f{number}")
        synth.write_frame(Path(f"{stem}.overscan.tiff"), dpi, number, infrared=False)
        synth.write_frame(Path(f"{stem}-ir.overscan.tiff"), dpi, number, infrared=True)
        Path(f"{stem}.diag.json").write_text(json.dumps({"frame": number}))
        if mode != "coverage_fail":
            os.link(f"{stem}.overscan.tiff", f"{stem}.tiff")  # the same pixels, no second copy
    print(f"FAKE_DRIVER_PEAK_KIB={_peak_kib()}", flush=True)  # read by scripts/rehearse.py
    return 1 if mode == "coverage_fail" else 0


def _peak_kib() -> int:
    with open("/proc/self/status") as handle:
        return next((int(line.split()[1]) for line in handle if line.startswith("VmHWM:")), 0)


def main() -> int:
    parser = argparse.ArgumentParser(prog="of135i")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("--frames")
    scan.add_argument("--dpi", type=int)
    scan.add_argument("--ir", action="store_true")
    scan.add_argument("--no-cal-data", action="store_true")
    scan.add_argument("--dark-fallback")
    scan.add_argument("--slides")
    scan.add_argument("-o", "--output", required=True)
    sub.add_parser("doctor")
    sub.add_parser("eject")
    load = sub.add_parser("load")
    load.add_argument("--next-strip", action="store_true")
    args = parser.parse_args()
    if args.command == "doctor":
        _trace("doctor start")
        time.sleep(float(os.environ.get("FAKE_DRIVER_DOCTOR_DELAY", "0")))
        addr = os.environ.get("FAKE_DRIVER_ADDR", "18")
        print(f"INFO of135i: opened device bus=3 addr={addr} (read-only session)")
        print(DOCTOR[os.environ.get("FAKE_DRIVER_DOCTOR", "idle")], end="")
        print("x" * int(os.environ.get("FAKE_DRIVER_DOCTOR_PAD", "0")))
        _trace("doctor end")
        return int(os.environ.get("FAKE_DRIVER_DOCTOR_EXIT", "0"))
    if args.command == "eject":
        _trace("eject")
        print("ejected")
        return int(os.environ.get("FAKE_DRIVER_EJECT_EXIT", "0"))
    if args.command == "load":
        return _load(args)
    return _scan(args)


def _load(args: argparse.Namespace) -> int:
    _trace("load start")
    argv_log = os.environ.get("FAKE_DRIVER_ARGV")
    if argv_log:
        Path(argv_log).write_text(json.dumps(sys.argv[1:]))
    if not args.next_strip:  # the interactive first load: never run by the app
        print("press Enter when the holder is in", flush=True)
        return 2
    time.sleep(float(os.environ.get("FAKE_DRIVER_LOAD_DELAY", "0")))
    code = int(os.environ.get("FAKE_DRIVER_LOAD_EXIT", "0"))
    print("loaded" if code == 0 else "ERROR of135i: no strip found", flush=True)
    _trace("load end")
    return code


if __name__ == "__main__":
    sys.exit(main())
