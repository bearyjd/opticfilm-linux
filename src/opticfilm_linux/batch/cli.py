"""``ofscan batch ...``: drive a batch from a terminal (and exercise the core without a window)."""

import argparse
import sys
from pathlib import Path

from opticfilm_linux.batch import review
from opticfilm_linux.batch.export import export_batch
from opticfilm_linux.batch.model import (
    ROTATIONS,
    BatchDefaults,
    SlideStatus,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.store import BatchStore
from opticfilm_linux.scanner.base import HolderControl, ScanError, Scanner
from opticfilm_linux.scanner.fake import FakeScanner
from opticfilm_linux.scanner.select import NEXT_STRIP_ENV, next_strip_enabled, real_scanner

EXIT_INTERRUPTED = 130


def add_orientation_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--rotate",
        type=int,
        choices=ROTATIONS,
        default=0,
        help="turn every slide clockwise by this many degrees",
    )
    parser.add_argument(
        "--mirror", action="store_true", help="flip every slide left-right (after turning it)"
    )


def add_batch_commands(commands: argparse._SubParsersAction) -> None:
    batch = commands.add_parser("batch", help="scan and review many trays in a batch folder")
    sub = batch.add_subparsers(dest="batch_command", required=True)

    new = sub.add_parser("new", help="create a batch folder")
    new.add_argument("directory", type=Path)
    new.add_argument("--name", required=True)
    new.add_argument("--album", default="")
    new.add_argument("--date", default=None)
    new.add_argument("--dpi", type=int, default=3600)
    add_orientation_options(new)

    status = sub.add_parser("status", help="list the trays")
    status.add_argument("directory", type=Path)

    scan = sub.add_parser("scan", help="scan trays into the batch")
    scan.add_argument("directory", type=Path)
    scan.add_argument("--fake", type=Path, help="replay the stored sweep in this folder")
    scan.add_argument("--count", type=int, default=1, help="how many trays to scan")

    for name, text in (
        ("eject", "eject the film holder"),
        ("load", "load the next holder without a terminal (needs OPTICFILM_NEXT_STRIP=1)"),
    ):
        holder = sub.add_parser(name, help=text)
        holder.add_argument("--fake", type=Path, help="use the stored sweep in this folder")

    accept = sub.add_parser("accept-all", help="accept every slide in every reviewed tray")
    accept.add_argument("directory", type=Path)

    export = sub.add_parser("export", help="write the accepted slides to a folder")
    export.add_argument("directory", type=Path)
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("--format", choices=["tiff16", "png8", "jpeg"], default="tiff16")


def _tray_line(tray: TrayRecord) -> str:
    if tray.stage is TrayStage.FAILED:
        return f"tray {tray.number}  failed  {tray.error}"
    counts = {status: 0 for status in SlideStatus}
    for slide in tray.slides:
        counts[slide.status] += 1
    return (
        f"tray {tray.number}  {tray.stage}  {counts[SlideStatus.ACCEPTED]} accepted, "
        f"{counts[SlideStatus.NEEDS_LOOK]} needs a look, {counts[SlideStatus.BLANK]} blank"
    )


def _print_status(store: BatchStore, only: set[int] | None = None) -> None:
    trays = [t for t in store.snapshot().trays if only is None or t.number in only]
    for tray in trays:
        print(_tray_line(tray))
    if not trays:
        print("no trays yet")


def _new(args: argparse.Namespace) -> int:
    defaults = BatchDefaults(dpi=args.dpi, rotation=args.rotate, mirror=args.mirror)
    BatchStore.create(args.directory, args.name, args.album, args.date, defaults).close()
    print(f"created batch {args.name!r} in {args.directory}")
    return 0


def _scan(args: argparse.Namespace) -> int:
    scanner: Scanner = FakeScanner(args.fake) if args.fake is not None else real_scanner()
    numbers: list[int] = []
    with BatchStore.open(args.directory) as store:
        runner = BatchRunner(store, scanner)
        try:
            runner.resume()
            for _ in range(args.count):
                numbers.append(runner.scan_tray())
                runner.wait_scans_idle()
            runner.wait_idle()
        except KeyboardInterrupt:
            print("interrupted: finishing the scan in progress", file=sys.stderr)
            runner.close()
            _print_status(store, set(numbers))
            return EXIT_INTERRUPTED
        finally:
            runner.close()
        _print_status(store, set(numbers))
        failed = [t.number for t in store.snapshot().trays if t.number in numbers and t.error]
    if failed:
        print(f"error: tray(s) {', '.join(map(str, failed))} failed", file=sys.stderr)
        return 2
    return 0


def _holder(args: argparse.Namespace) -> int:
    command = args.batch_command
    if command == "load" and not next_strip_enabled():
        raise ScanError(
            "loading the next holder is not proven on this scanner yet; "
            f"set {NEXT_STRIP_ENV}=1 to allow it, or load the holder by hand"
        )
    scanner: Scanner = FakeScanner(args.fake) if args.fake is not None else real_scanner()
    if not isinstance(scanner, HolderControl):
        raise ScanError("this scanner cannot move the holder")
    try:
        if command == "eject":
            scanner.eject()
            print("holder ejected")
        else:
            scanner.load_next_strip()
            print("holder loaded")
    except KeyboardInterrupt:  # the driver is already stopped; its state is unknown
        print("interrupted: the holder may be half-moved; check the scanner", file=sys.stderr)
        return EXIT_INTERRUPTED
    return 0


def run_batch(args: argparse.Namespace) -> int:
    command = args.batch_command
    if command == "new":
        return _new(args)
    if command == "scan":
        return _scan(args)
    if command in ("eject", "load"):
        return _holder(args)
    if command == "status":
        with BatchStore.open(args.directory, readonly=True) as store:
            _print_status(store)
        return 0
    with BatchStore.open(args.directory) as store:
        if command == "accept-all":
            review.accept_all(store)
            print("accepted every slide in every reviewed tray")
        else:
            result = export_batch(store, args.out, args.format)
            print(f"{len(result.written)} slides written")
    return 0
