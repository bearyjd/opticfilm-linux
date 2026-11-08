"""``ofscan batch run`` and ``free-held``: headless scan -> accept -> export -> free.

Drives the Qt-free ``AutoPipeline``. It never loads or ejects a holder and never confirms the
latch: those stay with the owner. Flags are saved into the batch before any scan, because the
pipeline marks a tray manual (``auto="off"``) when auto-export is off at the moment it arms it.
"""

import argparse
import dataclasses
import sys
from pathlib import Path

from opticfilm_linux.app.auto import MISSING_DESTINATION, READY, AutoPipeline
from opticfilm_linux.app.batch_rules import box_section_refusal, check_export_directory
from opticfilm_linux.batch.model import BatchManifest, TrayRecord
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.skipped import scan_positions
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.model import ALL_POSITIONS
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
    run.add_argument(
        "--skip",
        metavar="OPENINGS",
        help="holder openings 1-4 that are bare, e.g. 4 or 3,4: not scanned, for every tray of "
        "this command; their slide numbers stay reserved",
    )
    run.add_argument("--box", type=_positive, help="box number (an empty section only)")
    run.add_argument("--section", help="section a-f or 01-99 (an empty section only)")

    held = sub.add_parser("free-held", help="free the raw scans of the checked first tray")
    held.add_argument("directory", type=Path)
    held.add_argument("number", type=_positive, help="the held tray")
    held.add_argument("--yes", action="store_true", help="I checked the exported JPEGs")


def _skipped(args: argparse.Namespace) -> tuple[int, ...]:
    """The bare openings ``--skip`` names; raises ``RunRefused`` for anything the holder lacks."""
    if args.skip is None:
        return ()
    span = f"{ALL_POSITIONS[0]}-{ALL_POSITIONS[-1]}"
    parts = [part.strip() for part in args.skip.split(",")]
    if not all(parts):
        raise RunRefused("--skip names no openings; give e.g. --skip 4 or --skip 3,4")
    if not all(part.isdecimal() and int(part) in ALL_POSITIONS for part in parts):
        raise RunRefused(f"--skip {args.skip!r}: the holder has openings {span}")
    numbers = tuple(sorted(int(part) for part in parts))
    if len(set(numbers)) != len(numbers):
        raise RunRefused(f"--skip {args.skip!r}: name each opening once")
    try:
        scan_positions(numbers)
    except BatchError as exc:
        raise RunRefused(str(exc)) from exc
    return numbers


def _changes(manifest: BatchManifest, args: argparse.Namespace, out: Path | None) -> BatchManifest:
    """The manifest the flags ask for; raises ``RunRefused`` for anything not allowed."""
    wanted = {
        "export_dir": str(out) if out else None,
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


def _out_path(args: argparse.Namespace) -> Path | None:
    if args.out is None:
        return None
    try:
        return args.out.expanduser().absolute()
    except (RuntimeError, OSError) as exc:  # e.g. ~unknownuser, or no working directory
        raise RunRefused(f"cannot use --out {args.out}: {exc}") from exc


def _validate_flags(store: BatchStore, args: argparse.Namespace) -> Path | None:
    """Every pure refusal, computed on the loaded manifest; writes nothing."""
    out = _out_path(args)
    if out is not None:
        try:
            check_export_directory(store.directory, out)
        except BatchError as exc:
            raise RunRefused(str(exc)) from exc
    try:
        _changes(store.snapshot(), args, out)
    except ValueError as exc:
        raise RunRefused(str(exc)) from exc
    return out


def _persist_flags(store: BatchStore, args: argparse.Namespace, out: Path | None) -> None:
    before = store.snapshot()
    try:
        after = store.update(lambda manifest: _changes_or_refuse(manifest, args, out))
    except BatchError as exc:
        raise RunRefused(str(exc)) from exc
    for name in (*CHANGEABLE, "box", "section"):
        old = getattr(before.defaults if name in CHANGEABLE else before, name)
        new = getattr(after.defaults if name in CHANGEABLE else after, name)
        if old != new:
            print(f"changed {name}: {old!r} -> {new!r}")


def _changes_or_refuse(
    manifest: BatchManifest, args: argparse.Namespace, out: Path | None
) -> BatchManifest:
    try:
        return _changes(manifest, args, out)
    except (RunRefused, ValueError) as exc:
        raise BatchError(str(exc)) from exc


def _precheck(args: argparse.Namespace) -> None:
    if args.fake is None and args.count > 1:
        raise RunRefused(
            "the real scanner has one holder per run and there is no swap step; use --count 1"
        )
    if interrupted.is_pending():
        raise RunRefused(interrupted.refusal_text())


def _open_scanner(args: argparse.Namespace) -> Scanner:
    if args.fake is not None:
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


class _Interrupted(Exception):
    def __init__(self, numbers: tuple[int, ...], scan_in_flight: bool) -> None:
        super().__init__("interrupted")
        self.numbers = numbers
        self.scan_in_flight = scan_in_flight


def _drain_backlog(pipeline: AutoPipeline) -> None:
    """Older trays the pipeline considers eligible are exported or freed too."""
    for number in pipeline.resume():
        pipeline.run(number)


def _scan_trays(
    store: BatchStore, scanner: Scanner, count: int, skipped: tuple[int, ...] = ()
) -> list[int]:
    runner = BatchRunner(store, scanner)
    pipeline = AutoPipeline(store)
    numbers: list[int] = []
    scanning = False
    try:
        runner.resume()
        _drain_backlog(pipeline)
        for _ in range(count):
            scanning = True
            numbers.append(runner.scan_tray(skipped))
            runner.wait_scans_idle()
            scanning = False
            runner.wait_idle()
            if pipeline.arm(numbers[-1]):  # arm() writes auto="off" when auto-export is off
                pipeline.run(numbers[-1])
    except KeyboardInterrupt:
        raise _Interrupted(tuple(numbers), scanning) from None
    finally:
        runner.close()
    return numbers


def _touched(
    before: BatchManifest, after: BatchManifest, scanned: tuple[int, ...] | list[int]
) -> list[tuple[TrayRecord, TrayRecord | None]]:
    old = {t.number: t for t in before.trays}

    def key(tray: TrayRecord | None):
        return None if tray is None else (tray.stage, tray.auto, tray.raw_removed)

    return [
        (t, old.get(t.number))
        for t in after.trays
        if t.number in scanned or key(t) != key(old.get(t.number))
    ]


def _summary(tray: TrayRecord, old: TrayRecord | None) -> str:
    auto = tray.auto or "-"
    if old is not None and old.auto != tray.auto:
        auto = f"{old.auto or '-'}->{auto}"
    raw = "freed" if tray.raw_removed else "kept"
    return f"tray {tray.number}  {tray.stage}  auto={auto}  raw {raw}"


def _warn_manual_trays(store: BatchStore) -> None:
    manifest = store.snapshot()
    if not manifest.defaults.auto_export:
        return
    for tray in manifest.trays:
        if tray.auto == "off" and tray.stage in READY and not tray.raw_removed:
            print(
                f"warning: tray {tray.number} stays manual (auto=off) even with --auto-export; "
                "export it by hand",
                file=sys.stderr,
            )


def _report(
    store: BatchStore, directory: Path, before: BatchManifest, scanned: tuple[int, ...] | list[int]
) -> int:
    after = store.snapshot()
    touched = _touched(before, after, scanned)
    for tray, old in touched:
        print(_summary(tray, old))
    bad = [t for t, _ in touched if t.error or t.auto == "failed"]
    for tray in bad:
        print(f"error: tray {tray.number} failed: {tray.error or tray.auto_error}", file=sys.stderr)
    if not after.defaults.auto_export:
        print("auto-export is off: the tray(s) wait in review; export is manual")
    for tray, _ in touched:
        if tray.auto == "held":
            print(
                f"raw kept for tray {tray.number}; check the JPEGs, then run "
                f"`ofscan batch free-held {directory} {tray.number} --yes`"
            )
    return EXIT_FAILED if bad else 0


def _interrupted_exit(store: BatchStore, before: BatchManifest, stop: _Interrupted) -> int:
    if stop.scan_in_flight:
        print(
            "interrupted: finishing the scan in progress. If this program is stopped before "
            "it ends, power-cycle the scanner, load the holder again and run: "
            f"{interrupted.CONFIRM_COMMAND}",
            file=sys.stderr,
        )
    else:
        print("interrupted: no scan was in progress", file=sys.stderr)
    for tray, old in _touched(before, store.snapshot(), stop.numbers):
        print(_summary(tray, old))
    return EXIT_INTERRUPTED


def run(args: argparse.Namespace) -> int:
    try:
        _precheck(args)
        skipped = _skipped(args)
        with BatchStore.open(args.directory) as store:
            out = _validate_flags(store, args)  # 1: pure refusals, nothing written
            scanner = _open_scanner(args)  # 2-3: scanner state; read-only refusals
            _persist_flags(store, args, out)  # 4: the only write; arm() reads these defaults
            _warn_manual_trays(store)
            before = store.snapshot()
            try:
                numbers = _scan_trays(store, scanner, args.count, skipped)
            except _Interrupted as stop:
                return _interrupted_exit(store, before, stop)
            return _report(store, args.directory, before, numbers)
    except RunRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except KeyboardInterrupt:
        print("interrupted: no scan was in progress", file=sys.stderr)
        return EXIT_INTERRUPTED


def _free_refusal(manifest: BatchManifest, pipeline: AutoPipeline, number: int) -> str | None:
    """Mirror the silent returns of ``confirm_free_held``, read-only, with a reason for each."""
    tray = next((t for t in manifest.trays if t.number == number), None)
    if tray is None:
        return f"tray {number} does not exist"
    if tray.auto != "held" or tray.raw_removed or tray.stage not in READY:
        return f"tray {number} is not held"
    candidate = pipeline._release_candidate(manifest)
    if candidate is None:
        return "no tray is waiting to be released"
    if number != candidate:
        return f"tray {number} is not the tray to release (tray {candidate} is)"
    if manifest.defaults.keep_raw:
        return "keep_raw is set, so raw scans are never freed"
    if not manifest.defaults.auto_export:
        return "auto-export is off, so raw scans are never freed automatically"
    return _review_refusal(pipeline, tray)


def _review_refusal(pipeline: AutoPipeline, tray: TrayRecord) -> str | None:
    waiting = pipeline._awaiting_review(tray)
    if waiting:
        return f"slides {', '.join(map(str, waiting))} await your review"
    excluded = pipeline._owner_excluded(tray)
    if excluded:
        return f"slides {', '.join(map(str, excluded))} were excluded by your decision"
    if pipeline._unreviewed_flags(tray):
        return "some slides have flags you have not reviewed"
    if any(s.preview_stale for s in tray.slides):
        return "previews are out of date; wait for them, then try again"
    return None


def free_held(args: argparse.Namespace) -> int:
    if not args.yes:
        print(
            "Only free the raw scans after you checked the exported JPEGs; "
            f"then run: ofscan batch free-held {args.directory} {args.number} --yes",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    with BatchStore.open(args.directory) as store:
        pipeline = AutoPipeline(store)
        reason = _free_refusal(store.snapshot(), pipeline, args.number)
        if reason is not None:
            print(f"refused: {reason}", file=sys.stderr)
            return EXIT_REFUSED
        before = store.snapshot()
        pipeline.confirm_free_held(args.number)
        _drain_backlog(pipeline)
        after = store.snapshot()
        for tray, old in _touched(before, after, [args.number]):
            print(_summary(tray, old))
        tray = next(t for t in after.trays if t.number == args.number)
        if not tray.raw_removed:
            reason = tray.auto_error or "verification or cleanup did not pass"
            print(f"error: raw of tray {args.number} was kept: {reason}", file=sys.stderr)
            return EXIT_FAILED
    print(f"freed the raw scans of tray {args.number}")
    return 0
