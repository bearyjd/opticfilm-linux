#!/usr/bin/env python3
"""Production tray loop for mounted slides (4 openings per holder tray).

Drives the OpticFilm 135i CLI driver (`python3 -m of135i load|scan|eject`, cwd =
the driver worktree) one tray at a time, checks each opening's `.diag.json`
sidecar, and appends one CSV row per opening to OUT_ROOT/tray_log.csv.

The loop STOPS (and sends nothing further to the scanner) on any anomaly:
a non-zero exit other than 4, a missing/unreadable sidecar, or poll_timeouts > 0.
Exit 4 (aperture coverage not verified, overscan frames kept) stops too, unless
--keep-going-on-unverified. Never uses a shell. Python 3.10+, stdlib only.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

LOG = logging.getLogger("scan_slide_tray")

DEFAULT_DRIVER_DIR = Path(
    "/var/home/user/Documents/vibe-code/plustek-slide-scanner"
    "/.local-research/publish/upstream-pr-slides-throughput"
)
OPENINGS = (1, 2, 3, 4)
MIN_FREE_BYTES = 5 * 1024**3
VALID_DPI = (600, 1200, 2400, 3600, 7200)
EXIT_COVERAGE_UNVERIFIED = 4
INTERRUPT_GRACE_S = 3.0
CSV_NAME = "tray_log.csv"
CSV_COLUMNS = (
    "utc",
    "tray",
    "opening",
    "status",
    "exit_code",
    "verified",
    "poll_timeouts",
    "feedl",
    "total_seconds",
    "scan_seconds",
    "wall_seconds",
    "cropped_path",
    "overscan_dropped",
)
# Statuses that mean "this tray is done"; resume numbering counts only these.
DONE_STATUSES = ("OK", "UNVERIFIED")
FEEDL_RE = re.compile(r"slide (\d+) \(\d+ dpi\):.*?FEEDL=(\d+)")
MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


class ConfigError(Exception):
    """Invalid arguments or an unsafe environment; nothing was sent."""


class ChildInterrupted(Exception):
    """Ctrl-C while a child ran; the child has since exited."""

    def __init__(self, returncode: int | None) -> None:
        super().__init__(returncode)
        self.returncode = returncode


class TrayInterrupted(Exception):
    def __init__(self, tray: int, phase: str) -> None:
        super().__init__(f"tray {tray} interrupted during {phase}")
        self.tray = tray
        self.phase = phase


@dataclasses.dataclass(frozen=True)
class Config:
    driver_dir: Path
    out_root: Path
    dpi: int = 3600
    overscan: float | None = None
    start_tray: int | None = None
    max_trays: int | None = None
    drop_overscan: bool = False
    keep_going_on_unverified: bool = False
    python: str = "python3"
    module: str = "of135i"


@dataclasses.dataclass(frozen=True)
class Sidecar:
    verified: bool
    poll_timeouts: int
    total_seconds: float | None
    scan_seconds: float | None
    feedl: int | None


@dataclasses.dataclass(frozen=True)
class Opening:
    tray: int
    opening: int
    status: str
    exit_code: int
    verified: bool | None = None
    poll_timeouts: int | None = None
    feedl: int | None = None
    total_seconds: float | None = None
    scan_seconds: float | None = None
    wall_seconds: float | None = None
    cropped_path: str = ""
    overscan_dropped: bool = False


# ---------------------------------------------------------------- arguments


def parse_args(argv: Sequence[str] | None = None) -> Config:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--driver-dir", type=Path, default=DEFAULT_DRIVER_DIR)
    p.add_argument("--out-root", type=Path, required=True)
    p.add_argument("--dpi", type=int, default=3600)
    p.add_argument("--overscan", type=float, metavar="MM", default=None)
    p.add_argument("--start-tray", type=int, default=None)
    p.add_argument("--max-trays", type=int, default=None)
    p.add_argument("--drop-overscan", action="store_true")
    p.add_argument("--keep-going-on-unverified", action="store_true")
    p.add_argument("--python", default="python3")
    p.add_argument("--module", default="of135i")
    ns = p.parse_args(argv)
    return Config(**vars(ns))


def validate_config(cfg: Config) -> None:
    if cfg.dpi not in VALID_DPI:
        raise ConfigError(f"--dpi must be one of {VALID_DPI}, got {cfg.dpi}")
    if cfg.overscan is not None and not 0 < cfg.overscan <= 10:
        raise ConfigError(f"--overscan must be in (0, 10] mm, got {cfg.overscan}")
    if cfg.start_tray is not None and not 1 <= cfg.start_tray <= 9999:
        raise ConfigError("--start-tray must be 1..9999")
    if cfg.max_trays is not None and cfg.max_trays < 1:
        raise ConfigError("--max-trays must be >= 1")
    if not MODULE_RE.match(cfg.module):
        raise ConfigError(f"--module is not a dotted module name: {cfg.module!r}")
    if not cfg.python or cfg.python.startswith("-"):
        raise ConfigError(f"--python is not a program name: {cfg.python!r}")
    if not cfg.driver_dir.is_dir():
        raise ConfigError(f"--driver-dir is not a directory: {cfg.driver_dir}")
    check_out_root(cfg.out_root)


def check_out_root(out_root: Path) -> None:
    """Refuse unless out_root exists, is writable and has >= 5 GB free."""
    if not out_root.is_dir():
        raise ConfigError(f"--out-root does not exist: {out_root}")
    if not os.access(out_root, os.W_OK | os.X_OK):
        raise ConfigError(f"--out-root is not writable: {out_root}")
    free = shutil.disk_usage(out_root).free
    if free < MIN_FREE_BYTES:
        raise ConfigError(
            f"only {free / 1024**3:.1f} GB free on {out_root}; need >= "
            f"{MIN_FREE_BYTES / 1024**3:.0f} GB. Refusing to scan."
        )


# ---------------------------------------------------------------- CSV log


def next_tray_from_csv(csv_path: Path) -> int:
    """Next tray = 1 + highest tray whose rows show it finished (OK/UNVERIFIED)."""
    if not csv_path.is_file():
        return 1
    highest = 0
    with csv_path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("status") not in DONE_STATUSES:
                continue
            try:
                highest = max(highest, int(row["tray"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise ConfigError(f"unreadable tray number in {csv_path}: {row!r}") from exc
    return highest + 1


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def append_rows(csv_path: Path, rows: Sequence[Opening]) -> None:
    new_file = not csv_path.exists()
    utc = datetime.now(UTC).isoformat(timespec="seconds")
    with csv_path.open("a", newline="") as fh:
        writer = csv.writer(fh)
        if new_file:
            writer.writerow(CSV_COLUMNS)
        for r in rows:
            writer.writerow(
                [
                    _cell(v)
                    for v in (
                        utc,
                        r.tray,
                        r.opening,
                        r.status,
                        r.exit_code,
                        r.verified,
                        r.poll_timeouts,
                        r.feedl,
                        r.total_seconds,
                        r.scan_seconds,
                        r.wall_seconds,
                        r.cropped_path,
                        r.overscan_dropped,
                    )
                ]
            )
        fh.flush()
        os.fsync(fh.fileno())


# ---------------------------------------------------------------- driver calls


def driver_argv(cfg: Config, *args: str) -> list[str]:
    return [cfg.python, "-m", cfg.module, *args]


def scan_args(cfg: Config, out_path: Path) -> list[str]:
    args = [
        "scan",
        "--holder",
        "slide",
        "--frames",
        "1-4",
        "--dpi",
        str(cfg.dpi),
        "--ir",
        "--slide-positive",
    ]
    if cfg.overscan is not None:
        args += ["--overscan", str(cfg.overscan)]
    return args + ["-o", str(out_path)]


def _drain_child(proc: subprocess.Popen, grace: float) -> None:
    """After Ctrl-C: give the child its grace, SIGINT it once, then just wait."""
    signalled = False
    while proc.poll() is None:
        try:
            if signalled:
                proc.wait()
            else:
                proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            proc.send_signal(signal.SIGINT)
            signalled = True
        except KeyboardInterrupt:
            LOG.warning("still waiting for the driver to finish; do not unplug anything")


def run_child(
    argv: Sequence[str],
    cwd: Path,
    stderr_path: Path | None = None,
    grace: float = INTERRUPT_GRACE_S,
) -> int:
    """Run argv (never via a shell). stdin/stdout are inherited; stderr to a file if given.

    Ctrl-C never kills the child: warn, wait, SIGINT it once if it is still
    running after `grace` (a terminal Ctrl-C already reached it), then wait.
    """
    err = stderr_path.open("ab") if stderr_path else None
    try:
        proc = subprocess.Popen(list(argv), cwd=cwd, stderr=err)
        try:
            return proc.wait()
        except KeyboardInterrupt:
            LOG.warning(
                "Ctrl-C: the scanner may be mid-move. Waiting for the driver "
                "to finish; it is not being killed."
            )
            _drain_child(proc, grace)
            raise ChildInterrupted(proc.returncode) from None
    finally:
        if err:
            err.close()


# ---------------------------------------------------------------- results


def read_sidecar(path: Path) -> Sidecar | None:
    """Parse a .diag.json; None if missing/unreadable/lacking the required fields."""
    try:
        data = json.loads(path.read_text())
        polls = data["poll_timeouts"]
        verified = data["coverage"]["verified"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(polls, int) or isinstance(polls, bool):
        return None
    phases = data.get("phase_seconds")
    scan_s = phases.get("scan") if isinstance(phases, dict) else None
    feedl = data.get("feedl")
    return Sidecar(
        verified=verified is True,
        poll_timeouts=polls,
        total_seconds=_num(data.get("total_seconds")),
        scan_seconds=_num(scan_s),
        feedl=feedl if isinstance(feedl, int) and not isinstance(feedl, bool) else None,
    )


def _num(value: object) -> float | None:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    return float(value) if ok else None  # type: ignore[arg-type]


def parse_feedl(log_path: Path) -> dict[int, int]:
    try:
        text = log_path.read_text(errors="replace")
    except OSError:
        return {}
    return {int(m.group(1)): int(m.group(2)) for m in FEEDL_RE.finditer(text)}


def frame_paths(stem: Path, n: int) -> dict[str, Path]:
    """Driver naming: <stem>-f<N>[-ir][.overscan].tiff and <stem>-f<N>.diag.json."""
    base = f"{stem.name}-f{n}"
    d = stem.parent
    return {
        "vis": d / f"{base}.tiff",
        "ir": d / f"{base}-ir.tiff",
        "vis_over": d / f"{base}.overscan.tiff",
        "ir_over": d / f"{base}-ir.overscan.tiff",
        "diag": d / f"{base}.diag.json",
    }


def _nonempty(p: Path) -> bool:
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def assess_opening(
    tray: int, n: int, stem: Path, exit_code: int, wall: float, feedls: dict[int, int]
) -> Opening:
    paths = frame_paths(stem, n)
    side = read_sidecar(paths["diag"])
    base = Opening(
        tray=tray,
        opening=n,
        status="",
        exit_code=exit_code,
        wall_seconds=wall,
        cropped_path=str(paths["vis"]),
    )
    if exit_code not in (0, EXIT_COVERAGE_UNVERIFIED):
        return dataclasses.replace(base, status="SCAN_FAILED")
    if side is None:
        return dataclasses.replace(base, status="NO_SIDECAR")
    status = "OK" if side.verified and exit_code == 0 else "UNVERIFIED"
    if side.poll_timeouts > 0:
        status = "POLL_TIMEOUT"
    return dataclasses.replace(
        base,
        status=status,
        verified=side.verified,
        poll_timeouts=side.poll_timeouts,
        feedl=feedls.get(n, side.feedl),
        total_seconds=side.total_seconds,
        scan_seconds=side.scan_seconds,
    )


def drop_overscan_frames(opening: Opening, stem: Path) -> Opening:
    """Delete the two overscan frames only if every safety condition holds."""
    paths = frame_paths(stem, opening.opening)
    safe = (
        opening.status == "OK"
        and opening.exit_code == 0
        and opening.verified is True
        and _nonempty(paths["vis"])
        and _nonempty(paths["ir"])
    )
    if not safe:
        return opening
    dropped = False
    for key in ("vis_over", "ir_over"):
        p = paths[key]
        if p.is_file() and not p.is_symlink():
            p.unlink()
            LOG.info("dropped overscan frame %s", p)
            dropped = True
    return dataclasses.replace(opening, overscan_dropped=dropped)


# ---------------------------------------------------------------- tray loop


def tray_dir_for(cfg: Config, tray: int) -> Path:
    return cfg.out_root / f"tray{tray:04d}"


def _stop(msg: str) -> None:
    LOG.critical("STOP: %s", msg)
    LOG.critical(
        "Inspect the tray and %s before doing anything else. Power-cycle the "
        "scanner if the scan log says so. Nothing further is being sent to "
        "the scanner.",
        "scan.log and the sidecars",
    )


def load_and_scan(cfg: Config, tray: int) -> tuple[int, float, Path]:
    """Steps 2-3. Returns (scan exit code, wall seconds, stem). Raises on a load failure."""
    tdir = tray_dir_for(cfg, tray)
    try:
        rc = run_child(driver_argv(cfg, "load"), cfg.driver_dir)
    except ChildInterrupted:
        raise TrayInterrupted(tray, "load") from None
    if rc != 0:
        raise ConfigError(f"`load` exited {rc} for tray {tray}; aborting the loop")
    tdir.mkdir(parents=True)
    stem = tdir / f"tray{tray:04d}"
    started = time.monotonic()
    try:
        scan_rc = run_child(
            driver_argv(cfg, *scan_args(cfg, tdir / f"tray{tray:04d}.tiff")),
            cfg.driver_dir,
            stderr_path=tdir / "scan.log",
        )
    except ChildInterrupted:
        raise TrayInterrupted(tray, "scan") from None
    return scan_rc, time.monotonic() - started, stem


def decide(openings: Sequence[Opening], cfg: Config) -> str | None:
    """Return a stop message, or None to carry on."""
    bad = [o for o in openings if o.status not in ("OK", "UNVERIFIED")]
    if bad:
        why = ", ".join(f"opening {o.opening}: {o.status}" for o in bad)
        return f"tray {openings[0].tray} needs inspection ({why})"
    unverified = [o.opening for o in openings if o.status == "UNVERIFIED"]
    if unverified and not cfg.keep_going_on_unverified:
        return (
            f"tray {openings[0].tray} openings {unverified} UNVERIFIED (overscan "
            "kept). The holder is still in the scanner: eject it by hand."
        )
    return None


def process_tray(cfg: Config, tray: int) -> tuple[tuple[Opening, ...], str | None]:
    scan_rc, wall, stem = load_and_scan(cfg, tray)
    feedls = parse_feedl(stem.parent / "scan.log")
    openings = tuple(assess_opening(tray, n, stem, scan_rc, wall, feedls) for n in OPENINGS)
    reason = decide(openings, cfg)
    if reason:
        return openings, reason
    if cfg.drop_overscan:
        openings = tuple(drop_overscan_frames(o, stem) for o in openings)
    try:
        eject_rc = run_child(driver_argv(cfg, "eject"), cfg.driver_dir)
    except ChildInterrupted:
        raise TrayInterrupted(tray, "eject") from None
    return openings, (f"`eject` exited {eject_rc}" if eject_rc != 0 else None)


def ask_tray(tray: int, input_fn: Callable[[str], str]) -> bool:
    """True to proceed; False on q / EOF."""
    prompt = f"Seat holder for tray {tray} and press Enter to load (q to quit): "
    try:
        return input_fn(prompt).strip().lower() not in ("q", "quit")
    except EOFError:
        return False


def run_loop(cfg: Config, input_fn: Callable[[str], str]) -> int:
    csv_path = cfg.out_root / CSV_NAME
    tray = cfg.start_tray if cfg.start_tray is not None else next_tray_from_csv(csv_path)
    done = 0
    while cfg.max_trays is None or done < cfg.max_trays:
        check_out_root(cfg.out_root)
        if tray_dir_for(cfg, tray).exists():
            raise ConfigError(
                f"{tray_dir_for(cfg, tray)} already exists; move it aside "
                "or pick another --start-tray. Refusing to overwrite."
            )
        if not ask_tray(tray, input_fn):
            LOG.info("quit requested; stopping before tray %d", tray)
            return 0
        openings, reason = process_tray(cfg, tray)
        append_rows(csv_path, openings)
        if reason:
            _stop(reason)
            return 1
        LOG.info("tray %d done: %s", tray, ", ".join(f"{o.opening}={o.status}" for o in openings))
        tray, done = tray + 1, done + 1
    return 0


def main(argv: Sequence[str] | None = None, input_fn: Callable[[str], str] = input) -> int:
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        cfg = parse_args(argv)
        validate_config(cfg)
        return run_loop(cfg, input_fn)
    except ConfigError as exc:
        LOG.error("%s", exc)
        return 2
    except TrayInterrupted as exc:
        LOG.error(
            "Interrupted: tray %d was in flight during %s. The scanner/holder may "
            "be mid-state; inspect before restarting. No CSV row was written.",
            exc.tray,
            exc.phase,
        )
        return 130
    except KeyboardInterrupt:
        LOG.error("Interrupted at the prompt; no tray in flight.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
