#!/usr/bin/env python3
"""Pre-flight check before a scanning session. Prints PASS/WARN/FAIL per check, then GO / NO-GO.

Usage: preflight.py --driver-dir DIR --dest DIR [--min-free-gb 50] [--min-tmp-free-gb 8]
                    [--skip-scanner]

Exit 0 = GO (no FAIL), 1 = NO-GO. Runs only these read-only commands, nothing else:
  systemctl --user is-active opticfilm-eject-watch.service
  <python> -c "import of135i"          (cwd = driver dir; never touches USB)
  <python> -m of135i doctor            (cwd = driver dir; the driver's read-only report)
Never runs scan / load / eject. Close the app and stop the Eject watcher before running it.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
WATCH_UNIT = "opticfilm-eject-watch.service"
CMD_TIMEOUT_S = 60
GB = 1024**3
PROBE_BYTES = 4096
LOCK_NAMES = ("batch.lock", ".tray_log.lock")
IDLE_HOMED = 0x22
LOADED_HIGH_BYTE = 0xDC


@dataclass(frozen=True)
class Result:
    status: str
    detail: str


Runner = Callable[[Sequence[str], Path | None], "subprocess.CompletedProcess[str]"]


def allowed_argvs() -> tuple[tuple[str, ...], ...]:
    """The only commands this script may run (all read-only)."""
    py = sys.executable
    return (
        ("systemctl", "--user", "is-active", WATCH_UNIT),
        (py, "-c", "import of135i"),
        (py, "-m", "of135i", "doctor"),
    )


def run_allowed(argv: Sequence[str], cwd: Path | None) -> subprocess.CompletedProcess[str]:
    """Run argv only if it is on the allow-list."""
    if tuple(argv) not in allowed_argvs():
        raise PermissionError(f"command not allow-listed: {list(argv)}")
    return subprocess.run(
        list(argv), cwd=cwd, capture_output=True, text=True, timeout=CMD_TIMEOUT_S, check=False
    )


@dataclass
class Context:
    driver_dir: Path
    dest: Path
    min_free_gb: float = 50.0
    min_tmp_free_gb: float = 8.0
    skip_scanner: bool = False
    tmp_dir: Path = field(default_factory=lambda: Path(tempfile.gettempdir()))
    proc_root: Path = Path("/proc")
    run: Runner = run_allowed
    self_pid: int = field(default_factory=os.getpid)


Check = Callable[[Context], Result]


def write_probe(directory: Path) -> str | None:
    """Write and fsync a small file in directory. Returns an error text, or None on success."""
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".preflight-", delete=True) as f:
            f.write(b"\0" * PROBE_BYTES)
            f.flush()
            os.fsync(f.fileno())
    except OSError as exc:
        return f"cannot write+fsync a small file ({exc})"
    return None


def disk_result(label: str, directory: Path, min_gb: float) -> Result:
    if not directory.is_dir():
        return Result(FAIL, f"{label} {directory} is not a directory")
    try:
        st = os.statvfs(directory)
    except OSError as exc:
        return Result(FAIL, f"{label}: statvfs failed ({exc})")
    free_gb = st.f_bavail * st.f_frsize / GB
    if free_gb < min_gb:
        return Result(FAIL, f"{label} {directory}: {free_gb:.1f} GB free, need {min_gb:g}")
    error = write_probe(directory)
    if error:
        return Result(FAIL, f"{label} {directory}: {error}")
    return Result(PASS, f"{label} {directory}: {free_gb:.1f} GB free, small write+fsync ok")


def check_disk_dest(ctx: Context) -> Result:
    return disk_result("dest", ctx.dest, ctx.min_free_gb)


def check_disk_tmp(ctx: Context) -> Result:
    return disk_result("temp", ctx.tmp_dir, ctx.min_tmp_free_gb)


def check_dest(ctx: Context) -> Result:
    dest = Path(os.path.abspath(ctx.dest))
    if not dest.exists():
        return Result(FAIL, f"{dest} does not exist")
    if not dest.is_dir():
        return Result(FAIL, f"{dest} is not a directory")
    if not os.access(dest, os.W_OK | os.X_OK):
        return Result(FAIL, f"{dest} is not writable")
    if dest.is_symlink() and dest.resolve().parent != dest.parent.resolve():
        return Result(FAIL, f"{dest} is a symlink leading outside its parent ({dest.resolve()})")
    return Result(PASS, f"{dest} exists, is a writable directory")


def check_watcher(ctx: Context) -> Result:
    try:
        proc = ctx.run(("systemctl", "--user", "is-active", WATCH_UNIT), None)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Result(WARN, f"systemctl unavailable ({exc}); cannot tell if {WATCH_UNIT} runs")
    state = proc.stdout.strip()
    if state in ("active", "activating", "reloading"):
        return Result(FAIL, f"{WATCH_UNIT} is {state}; it grabs the scanner (stop it)")
    if state in ("inactive", "failed", "deactivating"):
        return Result(PASS, f"{WATCH_UNIT} is {state}")
    return Result(WARN, f"{WATCH_UNIT}: unrecognised state {state or proc.stderr.strip()!r}")


def check_driver(ctx: Context) -> Result:
    if not (ctx.driver_dir / "of135i").is_dir():
        return Result(FAIL, f"{ctx.driver_dir} has no of135i package")
    try:
        proc = ctx.run((sys.executable, "-c", "import of135i"), ctx.driver_dir)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Result(FAIL, f"import of135i could not run ({exc})")
    if proc.returncode != 0:
        tail = (proc.stderr.strip().splitlines() or ["no output"])[-1]
        return Result(FAIL, f"import of135i failed: {tail}")
    return Result(PASS, f"import of135i works from {ctx.driver_dir}")


def interpret_doctor(returncode: int, out: str) -> Result:
    """Map the read-only doctor report to a result. Anything unclear is UNKNOWN (WARN)."""
    state = re.search(r"reg 0x01 = 0x([0-9a-fA-F]{2})", out)
    if state is None:
        if returncode != 0 and re.search(r"no device|not found|not connected", out, re.I):
            return Result(FAIL, "scanner not connected (powered off or USB unplugged)")
        return Result(WARN, "scanner state UNKNOWN: doctor output unparsable")
    code = int(state.group(1), 16)
    if code == 0x00:
        return Result(FAIL, "scanner freshly powered, holder not loaded: do the cold start")
    if code != IDLE_HOMED:
        return Result(FAIL, f"scanner not idle-homed (reg 0x01 = 0x{code:02x}): power cycle")
    present = re.search(r"present:\s*(True|False)", out)
    word = re.search(r"status word:\s*0x([0-9a-fA-F]{4})", out)
    if not (present and word):
        return Result(WARN, "scanner idle-homed, holder state UNKNOWN: report incomplete")
    if present.group(1) == "True" and int(word.group(1), 16) >> 8 == LOADED_HIGH_BYTE:
        return Result(PASS, "scanner idle-homed, holder loaded")
    return Result(WARN, "scanner idle-homed, holder not loaded")


def check_scanner(ctx: Context) -> Result:
    if ctx.skip_scanner:
        return Result(WARN, "scanner state SKIPPED (--skip-scanner)")
    try:
        proc = ctx.run((sys.executable, "-m", "of135i", "doctor"), ctx.driver_dir)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Result(WARN, f"scanner state UNKNOWN: doctor could not run ({exc})")
    return interpret_doctor(proc.returncode, proc.stdout + proc.stderr)


def is_scanner_user(args: Sequence[str]) -> bool:
    """True for an of135i driver process, the ofscan CLI or the OpticFilm app."""
    for i, arg in enumerate(args):
        name = os.path.basename(arg)
        if name in ("of135i", "ofscan") or "opticfilm_linux.app" in arg:
            return True
        if arg == "-m" and args[i + 1 : i + 2] == ["of135i"]:
            return True
    return False


def check_processes(ctx: Context) -> Result:
    try:
        entries = [p for p in ctx.proc_root.iterdir() if p.name.isdigit()]
    except OSError as exc:
        return Result(WARN, f"cannot read {ctx.proc_root} ({exc})")
    holders: list[str] = []
    for entry in entries:
        if int(entry.name) == ctx.self_pid:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue  # exited meanwhile, or not ours to read
        args = [a for a in raw.decode(errors="replace").split("\0") if a]
        if is_scanner_user(args):
            holders.append(f"pid {entry.name} ({' '.join(args)[:60]})")
    if holders:
        return Result(FAIL, "other process may hold the scanner: " + "; ".join(holders))
    return Result(PASS, "no of135i / ofscan / OpticFilm app process running")


def lock_is_held(path: Path) -> bool:
    with open(path) as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


def check_locks(ctx: Context) -> Result:
    held: list[str] = []
    for name in LOCK_NAMES:
        path = ctx.dest / name
        if not path.is_file():
            continue
        try:
            if lock_is_held(path):
                held.append(name)
        except OSError as exc:
            return Result(WARN, f"cannot test lock {path} ({exc})")
    if held:
        return Result(FAIL, f"lock held in {ctx.dest}: {', '.join(held)} (batch open elsewhere)")
    return Result(PASS, f"no held lock in {ctx.dest}")


CHECKS: dict[str, Check] = {
    "disk-dest": check_disk_dest,
    "disk-tmp": check_disk_tmp,
    "dest": check_dest,
    "eject-watcher": check_watcher,
    "driver": check_driver,
    "scanner": check_scanner,
    "processes": check_processes,
    "locks": check_locks,
}


def run_checks(ctx: Context, checks: Mapping[str, Check]) -> list[tuple[str, Result]]:
    results: list[tuple[str, Result]] = []
    for name, check in checks.items():
        try:
            result = check(ctx)
        except Exception as exc:  # a crashing check must not hide the others
            result = Result(FAIL, f"check crashed: {type(exc).__name__}: {exc}")
        results.append((name, result))
    return results


def verdict(results: Sequence[tuple[str, Result]]) -> str:
    return "NO-GO" if any(r.status == FAIL for _, r in results) else "GO"


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Pre-flight check before a scanning session.")
    p.add_argument("--driver-dir", type=Path, required=True)
    p.add_argument("--dest", type=Path, required=True)
    p.add_argument("--min-free-gb", type=float, default=50.0)
    p.add_argument("--min-tmp-free-gb", type=float, default=8.0)
    p.add_argument("--skip-scanner", action="store_true", help="do not query the scanner")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None, checks: Mapping[str, Check] | None = None) -> int:
    args = parse_args(argv)
    ctx = Context(
        driver_dir=args.driver_dir,
        dest=args.dest,
        min_free_gb=args.min_free_gb,
        min_tmp_free_gb=args.min_tmp_free_gb,
        skip_scanner=args.skip_scanner,
    )
    results = run_checks(ctx, CHECKS if checks is None else checks)
    for name, result in results:
        print(f"{result.status:4}  {name}: {result.detail}")
    final = verdict(results)
    print(final)
    return 0 if final == "GO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
