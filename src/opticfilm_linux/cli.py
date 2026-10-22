"""Command line entry point for ``ofscan``."""

import argparse
import sys
from pathlib import Path

from opticfilm_linux import __version__
from opticfilm_linux.model import RawSweep, ScanSettings
from opticfilm_linux.output import OutputSpec
from opticfilm_linux.pipeline.run import ProcessOptions, RunResult, process_sweep
from opticfilm_linux.pipeline.split import SlideSplitError
from opticfilm_linux.scanner.base import ScanError
from opticfilm_linux.scanner.community import CommunityDriverScanner
from opticfilm_linux.scanner.fake import FakeScanner
from opticfilm_linux.scanner.usb import find_devices
from opticfilm_linux.sweepstore import SweepFormatError, load_sweep

EXIT_ERROR = 2


def _add_output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--out", type=Path, required=True, help="folder for finished slides")
    parser.add_argument("--album", default="", help="album name used in file names")
    parser.add_argument("--date", default=None, help="date text used in file names")
    parser.add_argument("--format", choices=["tiff16", "png8", "jpeg"], default="tiff16")
    parser.add_argument("--mode", choices=["positive", "negative"], default="positive")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ofscan",
        description="Scan and enhance slides from the Plustek OpticFilm 135i.",
    )
    parser.add_argument("--version", action="version", version=f"ofscan {__version__}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("devices", help="list supported scanners (read-only)")
    scan = commands.add_parser("scan", help="scan a holder and process it")
    scan.add_argument("--fake", type=Path, help="replay the stored sweep in this folder")
    scan.add_argument("--dpi", type=int, default=3600)
    _add_output_options(scan)
    process = commands.add_parser("process", help="process a stored sweep")
    process.add_argument("sweep", type=Path, help="folder holding a stored sweep")
    _add_output_options(process)
    return parser


def _load_sweep(args: argparse.Namespace) -> RawSweep:
    if args.command == "process":
        return load_sweep(args.sweep)
    settings = ScanSettings(dpi=args.dpi, mode=args.mode)
    if args.fake is not None:
        return FakeScanner(args.fake).scan_holder(settings)
    devices = find_devices()
    if not devices:
        raise ScanError("no supported scanner found; plug it in or use --fake SWEEP_DIR")
    return CommunityDriverScanner(devices[0]).scan_holder(settings)


def _list_devices() -> int:
    devices = find_devices()
    if not devices:
        print("no supported scanner found")
    for device in devices:
        print(
            f"{device.vendor_id:04x}:{device.product_id:04x} {device.name}  "
            f"bus {device.bus} address {device.address}"
        )
    return 0


def _summary(result: RunResult) -> str:
    text = f"{len(result.written)} slides written, {result.blank_skipped} blank skipped"
    if result.blank_positions:
        label = "position" if len(result.blank_positions) == 1 else "positions"
        text += f" ({label} {', '.join(str(p) for p in result.blank_positions)})"
    return text


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "devices":
        return _list_devices()
    try:
        out = OutputSpec(directory=args.out, fmt=args.format, album=args.album, date=args.date)
        result = process_sweep(_load_sweep(args), ProcessOptions(mode=args.mode), out)
    except (SweepFormatError, ScanError, SlideSplitError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(_summary(result))
    return 0
