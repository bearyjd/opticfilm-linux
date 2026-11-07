"""``ofscan batch run`` and ``free-held``: headless scan -> accept -> export -> free.

Drives the Qt-free ``AutoPipeline``. It never loads or ejects a holder and never confirms the
latch: those stay with the owner. Flags are saved into the batch before any scan, because the
pipeline marks a tray manual (``auto="off"``) when auto-export is off at the moment it arms it.
"""

import argparse
import dataclasses
import sys
from pathlib import Path

from opticfilm_linux.app.auto import MISSING_DESTINATION, AutoPipeline
from opticfilm_linux.app.batch_rules import box_section_refusal, check_export_directory
from opticfilm_linux.batch.model import BatchManifest, TrayRecord
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.scanner import interrupted
from opticfilm_linux.scanner.base import ScanError, Scanner, ScannerStatus
from opticfilm_linux.scanner.fake import FakeScanner
from opticfilm_linux.scanner.holder_latch import HolderLatch, load_latch
from opticfilm_linux.scanner.select import real_scanner

EXIT_FAILED = 2
EXIT_REFUSED = 3
EXIT_INTERRUPTED = 130
CHANGEABLE = ("export_dir", "jpeg_size", "auto_export", "keep_raw")


class RunRefused(Exception):
    """The run was refused before any scanner command or manifest change."""


def _positive(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        raise argparse.ArgumentTypeError(f"{text!r} is not a positive whole number")
    return value


def add_run_commands(sub: argparse._SubParsersAction) -> None:
    run = sub.add_parser(
        "run", help="scan a tray, accept, export and free it without a window (no load/eject)"
    )
    run.add_argument("directory", type=Path)
    run.add_argument("--fake", type=Path, help="replay the stored sweep in this folder")
    run.add_argument("--count", type=_positive, default=1, help="trays to scan (fake only if >1)")
    run.add_argument("--out", type=Path, help="album folder for the exported JPEGs (saved)")
    run.add_argument("--jpeg-size", choices=["full", "3600"], help="JPEG size (saved)")
    run.add_argument("--auto-export", action=argparse.BooleanOptionalAction, default=None)
    run.add_argument("--keep-raw", action=argparse.BooleanOptionalAction, default=None)
    run.add_argument("--box", type=_positive, help="box number (an empty section only)")
    run.add_argument("--section", help="section a-f or 01-99 (an empty section only)")

    held = sub.add_parser("free-held", help="free the raw scans of the checked first tray")
    held.add_argument("directory", type=Path)
    held.add_argument("number", type=_positive, help="the held tray")
    held.add_argument("--yes", action="store_true", help="I checked the exported JPEGs")


def _changes(manifest: BatchManifest, args: argparse.Namespace) -> BatchManifest:
    """The manifest the flags ask for; raises ``RunRefused`` for anything not allowed."""
    wanted = {
        "export_dir": str(args.out.expanduser().absolute()) if args.out else None,
        "jpeg_size": args.jpeg_size,
        "auto_export": args.auto_export,
        "keep_raw": args.keep_raw,
    }
    given = {k: v for k, v in wanted.items() if v is not None}
    defaults = dataclasses.replace(manifest.defaults, **given)
    if defaults.auto_export and not defaults.export_dir:
        raise RunRefused(MISSING_DESTINATION + " (pass --out)")
    automatic = defaults.auto_export or any(t.auto not in ("", "off") for t in manifest.trays)
    if manifest.first_tray_released and automatic and defaults != manifest.defaults:
        raise RunRefused("export settings cannot change after releasing the first tray")
    refusal = box_section_refusal(manifest, args.box, args.section)
    if refusal is not None:
        raise RunRefused(refusal)
    return dataclasses.replace(
        manifest,
        defaults=defaults,
        box=args.box or manifest.box,
        section=args.section or manifest.section,
    )


def _persist_flags(store: BatchStore, args: argparse.Namespace) -> None:
    if args.out is not None:
        try:
            check_export_directory(store.directory, args.out.expanduser().absolute())
        except BatchError as exc:
            raise RunRefused(str(exc)) from exc
    before = store.snapshot()
    try:
        after = store.update(lambda manifest: _changes_or_refuse(manifest, args))
    except BatchError as exc:
        raise RunRefused(str(exc)) from exc
    for name in (*CHANGEABLE, "box", "section"):
        old = getattr(before.defaults if name in CHANGEABLE else before, name)
        new = getattr(after.defaults if name in CHANGEABLE else after, name)
        if old != new:
            print(f"changed {name}: {old!r} -> {new!r}")


def _changes_or_refuse(manifest: BatchManifest, args: argparse.Namespace) -> BatchManifest:
    try:
        return _changes(manifest, args)
    except (RunRefused, ValueError) as exc:
        raise BatchError(str(exc)) from exc


def _open_scanner(args: argparse.Namespace) -> Scanner:
    real = args.fake is None
    if real and args.count > 1:
        raise RunRefused(
            "the real scanner has one holder per run and there is no swap step; use --count 1"
        )
    if interrupted.is_pending():
        raise RunRefused(interrupted.refusal_text())
    if not real:
        return FakeScanner(args.fake)
    try:
        scanner = real_scanner()
        status = scanner.status()
    except ScanError as exc:
        raise RunRefused(str(exc)) from exc
    if status is not ScannerStatus.READY:
        raise RunRefused(
            f"the scanner is {status.value.replace('_', ' ')}, not ready; load the holder first "
            "(this command never loads or ejects)"
        )
    address = getattr(scanner, "usb_address", None)
    if address is not None and load_latch(*address) is HolderLatch.RELEASED:
        raise RunRefused("the holder latch is released; seat and latch the holder first")
    return scanner


def _scan_trays(store: BatchStore, scanner: Scanner, count: int) -> list[int]:
    runner = BatchRunner(store, scanner)
    pipeline = AutoPipeline(store)
    numbers: list[int] = []
    try:
        runner.resume()
        for number in pipeline.resume():
            pipeline.run(number)
        for _ in range(count):
            number = runner.scan_tray()
            numbers.append(number)
            runner.wait_scans_idle()
            runner.wait_idle()
            if pipeline.arm(number):  # arm() writes auto="off" when auto-export is off
                pipeline.run(number)
    except KeyboardInterrupt:
        _print_interrupted(store, numbers)
        raise _Interrupted from None
    finally:
        runner.close()
    return numbers


class _Interrupted(Exception):
    pass


def _print_interrupted(store: BatchStore, numbers: list[int]) -> None:
    print(
        "interrupted: finishing the scan in progress. If this program is stopped before "
        "it ends, power-cycle the scanner, load the holder again and run: "
        f"{interrupted.CONFIRM_COMMAND}",
        file=sys.stderr,
    )
    _print_summary(store, numbers)


def _summary(tray: TrayRecord) -> str:
    return f"tray {tray.number}  {tray.stage}  auto={tray.auto or '-'}  raw {_raw(tray)}"


def _raw(tray: TrayRecord) -> str:
    return "freed" if tray.raw_removed else "kept"


def _print_summary(store: BatchStore, numbers: list[int]) -> None:
    for tray in store.snapshot().trays:
        if tray.number in numbers:
            print(_summary(tray))


def _report(store: BatchStore, directory: Path, numbers: list[int]) -> int:
    _print_summary(store, numbers)
    manifest = store.snapshot()
    trays = [t for t in manifest.trays if t.number in numbers]
    bad = [t for t in trays if t.error or t.auto == "failed"]
    for tray in bad:
        print(f"error: tray {tray.number} failed: {tray.error or tray.auto_error}", file=sys.stderr)
    if not manifest.defaults.auto_export:
        print("auto-export is off: the tray(s) wait in review; export is manual")
    for tray in trays:
        if tray.auto == "held":
            print(
                f"raw kept for tray {tray.number}; check the JPEGs, then run "
                f"`ofscan batch free-held {directory} {tray.number} --yes`"
            )
    return EXIT_FAILED if bad else 0


def run(args: argparse.Namespace) -> int:
    try:
        with BatchStore.open(args.directory) as store:
            scanner = _open_scanner(args)
            _persist_flags(store, args)
            try:
                numbers = _scan_trays(store, scanner, args.count)
            except _Interrupted:
                return EXIT_INTERRUPTED
            return _report(store, args.directory, numbers)
    except RunRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED


def free_held(args: argparse.Namespace) -> int:
    if not args.yes:
        print(
            "Only free the raw scans after you checked the exported JPEGs; "
            f"then run: ofscan batch free-held {args.directory} {args.number} --yes",
            file=sys.stderr,
        )
        return EXIT_FAILED
    with BatchStore.open(args.directory) as store:
        pipeline = AutoPipeline(store)
        tray = next((t for t in store.snapshot().trays if t.number == args.number), None)
        if tray is None or tray.auto != "held":
            print(f"error: tray {args.number} is not held", file=sys.stderr)
            return EXIT_FAILED
        pipeline.confirm_free_held(args.number)
        for number in pipeline.resume():
            pipeline.run(number)
        tray = next(t for t in store.snapshot().trays if t.number == args.number)
        if not tray.raw_removed:
            reason = tray.auto_error or "verification did not pass"
            print(f"error: raw of tray {args.number} was kept: {reason}", file=sys.stderr)
            return EXIT_FAILED
    print(f"freed the raw scans of tray {args.number}")
    return 0
