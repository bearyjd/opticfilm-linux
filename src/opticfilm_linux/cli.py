"""Command line entry point for ``ofscan``."""

import argparse
import sys
from pathlib import Path

from opticfilm_linux import __version__
from opticfilm_linux.batch.cli import add_batch_commands, run_batch
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
    parser.add_argument("--sharpen", type=float, default=0.5, help="sharpening amount (0 = off)")
    parser.add_argument("--grain", type=float, default=0.3, help="grain reduction (0 = off)")
    parser.add_argument(
        "--color-strength", type=float, default=1.0, help="colour restoration, 0 to 1 (0 = off)"
    )
    parser.add_argument(
        "--no-infrared",
        action="store_true",
        help="do not use the infrared plane: skip dust removal",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ofscan",
        description="Scan and enhance slides from the Plustek OpticFilm 135i.",
    )
    parser.add_argument("--version", action="version", version=f"ofscan {__version__}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("devices", help="list supported scanners (read-only)")
    add_batch_commands(commands)
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
    settings = ScanSettings(dpi=args.dpi, mode=args.mode, infrared=not args.no_infrared)
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


def _check_strengths(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if not 0.0 <= getattr(args, "color_strength", 0.0) <= 1.0:
        parser.error("--color-strength must be between 0 and 1")
    for name in ("sharpen", "grain"):
        if getattr(args, name, 0.0) < 0:
            parser.error(f"--{name} must not be negative")


def _summary(result: RunResult) -> str:
    text = f"{len(result.written)} slides written, {result.blank_skipped} blank skipped"
    if result.blank_positions:
        label = "position" if len(result.blank_positions) == 1 else "positions"
        text += f" ({label} {', '.join(str(p) for p in result.blank_positions)})"
    return text


def _run_batch_command(args: argparse.Namespace) -> int:
    try:
        return run_batch(args)
    except (ValueError, LookupError, ScanError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _check_strengths(parser, args)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "devices":
        return _list_devices()
    if args.command == "batch":
        return _run_batch_command(args)
    try:
        out = OutputSpec(directory=args.out, fmt=args.format, album=args.album, date=args.date)
        options = ProcessOptions(
            mode=args.mode,
            remove_dust=not args.no_infrared,
            color_strength=args.color_strength,
            sharpen_amount=args.sharpen,
            grain_strength=args.grain,
        )
        result = process_sweep(_load_sweep(args), options, out)
    except (SweepFormatError, ScanError, SlideSplitError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(_summary(result))
    return 0
