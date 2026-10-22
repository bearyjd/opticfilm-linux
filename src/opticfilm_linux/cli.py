"""Command line entry point for ``ofscan``."""

import argparse
import sys
from pathlib import Path

from opticfilm_linux import __version__
from opticfilm_linux.model import RawSweep, ScanSettings
from opticfilm_linux.output import OutputSpec
from opticfilm_linux.pipeline.run import ProcessOptions, process_sweep
from opticfilm_linux.pipeline.split import SlideSplitError
from opticfilm_linux.scanner.base import NotSupportedYet, ScanError
from opticfilm_linux.scanner.fake import FakeScanner
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
    if args.fake is None:
        raise NotSupportedYet(
            "scanning the real device is not supported yet; use --fake SWEEP_DIR instead"
        )
    return FakeScanner(args.fake).scan_holder(ScanSettings(dpi=args.dpi, mode=args.mode))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    options = ProcessOptions(mode=args.mode)
    out = OutputSpec(directory=args.out, fmt=args.format, album=args.album, date=args.date)
    try:
        result = process_sweep(_load_sweep(args), options, out)
    except (SweepFormatError, ScanError, SlideSplitError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(f"{len(result.written)} slides written, {result.blank_skipped} blank skipped")
    return 0
