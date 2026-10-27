"""Offline verdict for a hardware-run output folder (a driver run's logs, diagnostics and TIFFs).

Reads files only; never touches the scanner. Layout it understands: ``*.log`` (driver log),
``*.diag.json`` (one per frame), ``*.tif``/``*.tiff`` (RGB plus a ``-ir`` plane per frame),
and an optional exit code (``exit_code`` file, ``RESULT.md`` "exited N", or an argument).
Missing or garbage files make a criterion FAIL with a message; nothing here raises.
"""

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import tifffile

DEFAULT_POSITION_TARGET_S = 10.44
DEFAULT_POSITION_TOLERANCE_S = 1.5
DEFAULT_MAX_POLL_TIMEOUTS = 1
PARKED_STATUS = "f855"

_POSITION_RE = re.compile(r"completion poll settled \w+ \(captured \w+\) after (\d+(?:\.\d+)?)s")
_CAL_WARNING_RE = re.compile(r"WARNING.*(?:cc55.*ad55|ad55.*cc55)", re.IGNORECASE)
_RESULT_EXIT_RE = re.compile(r"\bexit(?:ed)?\s*(?:code\s*)?[:=]?\s*(-?\d\w*)", re.IGNORECASE)


@dataclass(frozen=True)
class Result:
    name: str
    status: str  # PASS, FAIL, SKIP or NOTE (SKIP and NOTE never fail the verdict)
    detail: str


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _log_text(folder: Path) -> str | None:
    texts = [t for p in sorted(folder.glob("*.log")) if (t := _read_text(p)) is not None]
    return "\n".join(texts) if texts else None


def _load_diag(path: Path) -> dict | None:
    text = _read_text(path)
    try:
        data = json.loads(text) if text is not None else None
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _check_exit_code(folder: Path, given: int | None) -> Result:
    value: str | None = None if given is None else str(given)
    if value is None:
        for name in ("exit_code", "exit_code.txt"):
            if (text := _read_text(folder / name)) is not None:
                value = text.strip()
                break
    if value is None and (text := _read_text(folder / "RESULT.md")) is not None:
        if matches := _RESULT_EXIT_RE.findall(text):
            value = matches[-1]
    if value is None:
        return Result("exit-code", "SKIP", "exit code not recorded")
    if value == "0":
        return Result("exit-code", "PASS", "command exited 0")
    return Result("exit-code", "FAIL", f"command exit code {value!r}, expected 0")


def _tiff_problem(path: Path, is_rgb: bool) -> tuple[str | None, tuple | None]:
    try:
        pixels = tifffile.imread(path)  # decodes the pixel data: a truncated file must fail
        shape, dtype = tuple(pixels.shape), pixels.dtype
    except Exception as exc:  # any unreadable file is a failed criterion, not a crash
        return f"{path.name}: unreadable ({type(exc).__name__})", None
    if str(dtype) != "uint16":
        return f"{path.name}: dtype {dtype}, expected uint16", None
    if not shape or 0 in shape:
        return f"{path.name}: empty image {shape}", None
    if is_rgb and (len(shape) != 3 or shape[-1] != 3):
        return f"{path.name}: shape {shape}, expected (H, W, 3)", None
    return None, shape


def _check_tiffs(folder: Path) -> Result:
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".tif", ".tiff"))
    if not files:
        return Result("tiffs", "FAIL", "no TIFF files found")
    rgb = {p.name: p for p in files if not p.name.partition(".")[0].endswith("-ir")}
    problems: list[str] = []
    if not rgb:
        problems.append("no RGB TIFF found (only IR planes)")
    frames = {name.partition(".")[0] for name in rgb}
    for diag in sorted(folder.glob("*.diag.json")):
        if (prefix := diag.name.partition(".")[0]) not in frames:
            problems.append(f"{diag.name}: no RGB TIFF for frame {prefix}")
    shapes: dict[str, tuple] = {}
    for path in files:
        problem, shape = _tiff_problem(path, path.name in rgb)
        if problem:
            problems.append(problem)
        elif shape is not None:
            shapes[path.name] = shape
    for name, path in rgb.items():
        ir_name = _ir_name(path)
        ir = folder / ir_name
        if not ir.exists():
            problems.append(f"{name}: IR plane {ir_name} missing")
        elif name in shapes and ir_name in shapes and shapes[name] != shapes[ir_name]:
            problems.append(f"{name}: shape {shapes[name]} differs from IR {shapes[ir_name]}")
    if problems:
        return Result("tiffs", "FAIL", "; ".join(problems))
    return Result("tiffs", "PASS", f"{len(rgb)} RGB + IR pair(s), 16-bit, matching dimensions")


def _ir_name(rgb: Path) -> str:
    stem, _, rest = rgb.name.partition(".")
    return f"{stem}-ir" + (f".{rest}" if rest else "")


def _check_position(log: str | None, target: float, tolerance: float) -> Result:
    if log is None:
        return Result("position-wait", "FAIL", "no driver log to read the POSITION wait from")
    waits = [float(m.group(1)) for m in _POSITION_RE.finditer(log)]
    if not waits:
        return Result("position-wait", "FAIL", "no strict POSITION completion line in the log")
    longest = max(waits)
    window = f"{target - tolerance:.2f}..{target + tolerance:.2f} s"
    if abs(longest - target) <= tolerance + 1e-9:
        return Result("position-wait", "PASS", f"longest strict wait {longest:.2f} s in {window}")
    return Result("position-wait", "FAIL", f"longest strict wait {longest:.2f} s outside {window}")


def _check_park(diags: dict[str, dict | None], log: str | None) -> Result:
    if not diags:
        return Result("park", "FAIL", "no diagnostics file: park completion not evidenced")
    problems = []
    for name, diag in diags.items():
        waits = diag.get("park_waits") if diag else None
        if not isinstance(waits, dict):
            problems.append(f"{name}: no park_waits")
        elif waits.get("a_timed_out") is not False or waits.get("b_timed_out") is not False:
            problems.append(f"{name}: park wait timed out or unrecorded")
        elif waits.get("b_last") != PARKED_STATUS:
            problems.append(f"{name}: final status {waits.get('b_last')!r}, want {PARKED_STATUS}")
    if problems:
        return Result("park", "FAIL", "; ".join(problems))
    return Result("park", "PASS", f"park completed ({PARKED_STATUS}) in {len(diags)} frame(s)")


def _check_poll_timeouts(diags: dict[str, dict | None], limit: int) -> Result:
    if not diags:
        return Result("poll-timeouts", "FAIL", "no *.diag.json files found")
    problems = []
    for name, diag in diags.items():
        count = diag.get("poll_timeouts") if diag else None
        if not isinstance(count, int) or isinstance(count, bool):
            problems.append(f"{name}: poll_timeouts missing or unreadable")
        elif count < 0:
            problems.append(f"{name}: negative poll_timeouts {count}")
        elif count > limit:
            problems.append(f"{name}: {count} poll timeouts > {limit}")
    if problems:
        return Result("poll-timeouts", "FAIL", "; ".join(problems))
    return Result("poll-timeouts", "PASS", f"every frame has <= {limit} poll timeout(s)")


def _is_cal_pair(last: object, want: object) -> bool:
    return {str(last).lower(), str(want).lower()} == {"cc55", "ad55"}


def _check_calibration(diags: dict[str, dict | None], log: str | None) -> Result:
    if log is None:
        return Result("calibration", "FAIL", "no driver log to scan for calibration warnings")
    hits = [line.strip() for line in log.splitlines() if _CAL_WARNING_RE.search(line)]
    for name, diag in diags.items():
        details = (diag or {}).get("poll_timeout_details")
        for item in details if isinstance(details, list) else []:
            if isinstance(item, dict) and _is_cal_pair(item.get("last"), item.get("want")):
                hits.append(f"{name}: poll timeout cc55 vs ad55")
    if hits:
        return Result("calibration", "FAIL", f"cc55-vs-ad55 calibration warning: {hits[0]}")
    return Result("calibration", "PASS", "no cc55-vs-ad55 calibration warnings")


def _eject_note(folder: Path, log: str | None) -> Result | None:
    texts = [log or "", _read_text(folder / "RESULT.md") or ""]
    if not any("ejected" in text.lower() for text in texts):
        return None
    return Result(
        "eject-claim",
        "NOTE",
        "a file says 'ejected': never evidence; confirm the holder released physically",
    )


def check_folder(
    folder: Path,
    *,
    position_target: float = DEFAULT_POSITION_TARGET_S,
    position_tolerance: float = DEFAULT_POSITION_TOLERANCE_S,
    max_poll_timeouts: int = DEFAULT_MAX_POLL_TIMEOUTS,
    exit_code: int | None = None,
    require_exit_code: bool = False,
) -> list[Result]:
    """Check one run folder; one Result per criterion, never raises on bad files."""
    folder = Path(folder)
    try:
        return _check(
            folder, position_target, position_tolerance, max_poll_timeouts,
            exit_code, require_exit_code,
        )  # fmt: skip
    except OSError as exc:
        return [Result("folder", "FAIL", f"cannot read {folder}: {exc.strerror or exc}")]


def _diags_by_frame(folder: Path) -> dict[str, dict | None]:
    """Every *.diag.json, plus a None entry for each RGB TIFF frame that has none."""
    diags = {p.name: _load_diag(p) for p in sorted(folder.glob("*.diag.json"))}
    for tiff in sorted(folder.iterdir()):
        prefix = tiff.name.partition(".")[0]
        if tiff.suffix.lower() in (".tif", ".tiff") and not prefix.endswith("-ir"):
            diags.setdefault(f"{prefix}.diag.json", None)
    return diags


def _check(
    folder: Path,
    position_target: float,
    position_tolerance: float,
    max_poll_timeouts: int,
    exit_code: int | None,
    require_exit_code: bool,
) -> list[Result]:
    if not folder.is_dir():
        return [Result("folder", "FAIL", f"{folder} is not a directory")]
    log = _log_text(folder)
    diags = _diags_by_frame(folder)
    exit_result = _check_exit_code(folder, exit_code)
    if require_exit_code and exit_result.status == "SKIP":
        exit_result = Result("exit-code", "FAIL", "exit code not recorded but required")
    results = [
        exit_result,
        _check_tiffs(folder),
        _check_position(log, position_target, position_tolerance),
        _check_park(diags, log),
        _check_poll_timeouts(diags, max_poll_timeouts),
        _check_calibration(diags, log),
    ]
    if note := _eject_note(folder, log):
        results.append(note)
    return results


def all_passed(results: Sequence[Result]) -> bool:
    return all(r.status != "FAIL" for r in results)


def format_report(results: Sequence[Result]) -> str:
    lines = [f"{r.status:<5} {r.name:<14} {r.detail}" for r in results]
    lines.append(f"VERDICT: {'PASS' if all_passed(results) else 'FAIL'}")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline verdict for a hardware-run folder.")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--position-target", type=float, default=DEFAULT_POSITION_TARGET_S)
    parser.add_argument("--position-tolerance", type=float, default=DEFAULT_POSITION_TOLERANCE_S)
    parser.add_argument("--max-poll-timeouts", type=int, default=DEFAULT_MAX_POLL_TIMEOUTS)
    parser.add_argument("--exit-code", type=int, default=None, help="the command's exit code")
    parser.add_argument(
        "--require-exit-code", action="store_true", help="fail when no exit code is recorded"
    )
    args = parser.parse_args(argv)
    results = check_folder(
        args.folder,
        position_target=args.position_target,
        position_tolerance=args.position_tolerance,
        max_poll_timeouts=args.max_poll_timeouts,
        exit_code=args.exit_code,
        require_exit_code=args.require_exit_code,
    )
    report = format_report(results)
    sys.stdout.write(report.encode("ascii", "backslashreplace").decode("ascii"))
    return 0 if all_passed(results) else 1
