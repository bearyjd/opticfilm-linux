"""Offline report on poll timeouts recorded in hardware-run folders.

Reads files only; never touches the scanner. It classifies, from existing logs, (a) calibration
read-back poll timeouts and (b) cold-start move timeouts. Everything printed is a lead from the
logs, not proof of what the hardware did. Garbage files never raise: they just yield no findings.
"""

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

MAX_READ_BYTES = 32 * 1024 * 1024
ACK_BYTE = 0x55
# Status-byte bits, highest first. Unnamed bits are reported raw.
BIT_NAMES = (
    (0x80, "power_bit"),
    (0x40, "buffer_empty"),
    (0x20, "bit20"),
    (0x10, "bit10"),
    (0x08, "loader_sensor"),
    (0x04, "lamp_on"),
    (0x02, "bit02"),
    (0x01, "motor_enabled"),
)
MOTOR = 0x01
BUFFER_EMPTY = 0x40
OUTCOMES = ("ended-off-sensor", "still-busy", "no-busy-observed", "malformed", "other")
NO_HISTORY_NOTE = "cannot distinguish moved-and-stopped from never-started (no read history)"
HEADER = (
    "POLL REPORT: a lead from logs, not proof. Read-only; nothing here touches the scanner.\n"
    "A status word is a status byte plus a 0x55 ack byte; bits are read from the status byte."
)

_STATUS_RE = re.compile(r"(?:0x)?([0-9a-f]{2})([0-9a-f]{2})", re.IGNORECASE)
_HOW_RE = re.compile(r"[a-z][a-z_-]{0,19}")
_LOG_TIMEOUT_RE = re.compile(
    r"^\S*\s*WARNING\b.*?\bpoll\b.*?timed out after [\d.]+s \(last ([^,\s)]+), want ([^\s)]+)\)"
)
_LOG_READBACK_RE = re.compile(r"\bDEBUG\b.*?\bcr\b.*?\bgot (\S+) want (\S+)\s+--")
_EJECT_TIMEOUT_RE = re.compile(
    r"poll_status_word timed out after [\d.]+s: last ([^\s,]+), want ([^\s,]+)"
)
_HOMING_RE = re.compile(r"cold_init: homing round (\d+)/(\d+)")
_RESYNC_RE = re.compile(r"cold_init: resync status word (\S+) \(want ([^)\s]+)\)")
_FAILED_RE = re.compile(
    r"hardware session FAILED in operation (\w+), phase ([\w:.-]+), "
    r"after (\d{1,9}) writes \((\d{1,9}) execute pulses?\)"
)
_OUTCOME_RE = re.compile(
    r"\boutcome\b[\s=:]*['\"]?(" + "|".join(OUTCOMES) + r")(?![\w-])", re.IGNORECASE
)


@dataclass(frozen=True)
class Status:
    first: int
    second: int
    bits: tuple[str, ...]


def decode_status(value: object) -> Status | None:
    """Decode ``HHHH`` / ``0xHHHH`` (status byte, 0x55 ack); anything else is None."""
    if not isinstance(value, str):
        return None
    match = _STATUS_RE.fullmatch(value.strip())
    if match is None:
        return None
    first, second = int(match.group(1), 16), int(match.group(2), 16)
    if second != ACK_BYTE:
        return None
    return Status(first, second, tuple(name for mask, name in BIT_NAMES if first & mask))


def classify_calibration(last: object, want: object) -> str:
    """benign-baseline, acquisition-not-observed, other, or malformed (unreadable word).

    ``benign-baseline`` is judged on the wanted value alone (it is itself an idle/empty-buffer
    value); the observed bits are printed beside it so a reader can still see what was read.
    """
    got, wanted = decode_status(last), decode_status(want)
    if got is None or wanted is None:
        return "malformed"
    if wanted.first & BUFFER_EMPTY and not wanted.first & MOTOR:
        return "benign-baseline"
    if (
        wanted.first & MOTOR
        and not wanted.first & BUFFER_EMPTY
        and not got.first & MOTOR
        and got.first & BUFFER_EMPTY
    ):
        return "acquisition-not-observed"
    return "other"


def _bits_text(value: object) -> str:
    status = decode_status(value)
    if status is None:
        return "malformed"
    return ",".join(status.bits) or "no bits set"


def _shown(value: object) -> str:
    """A status word for display: normalised when decodable, else a fixed marker."""
    status = decode_status(value)
    return f"{status.first:02x}{status.second:02x}" if status else "malformed"


def _read_text(path: Path) -> str | None:
    try:
        with path.open("rb") as handle:
            return handle.read(MAX_READ_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return None


def _load_diag(path: Path) -> dict | None:
    text = _read_text(path)
    try:
        data = json.loads(text) if text is not None else None
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


@dataclass(frozen=True)
class CalEvent:
    source: str
    kind: str  # "timeout" or "mismatch"
    last: str
    want: str
    cls: str


def _cal_event(source: str, kind: str, last: object, want: object) -> CalEvent:
    return CalEvent(source, kind, _shown(last), _shown(want), classify_calibration(last, want))


def _log_cal_events(folder: Path) -> list[CalEvent]:
    events: list[CalEvent] = []
    for path in sorted(folder.glob("*.log")):
        for line in (_read_text(path) or "").splitlines():
            if m := _LOG_TIMEOUT_RE.search(line):
                events.append(_cal_event(path.name, "timeout", m.group(1), m.group(2)))
            elif m := _LOG_READBACK_RE.search(line):
                events.append(_cal_event(path.name, "mismatch", m.group(1), m.group(2)))
    return events


@dataclass(frozen=True)
class DiagSummary:
    name: str
    readable: bool
    poll_timeouts: int | None
    settle_how: tuple[tuple[str, int], ...]
    with_bits: int
    details: tuple[CalEvent, ...]


def _how_label(value: object) -> str:
    """A settle mode word for display; anything not a short plain word is hidden."""
    if isinstance(value, str) and _HOW_RE.fullmatch(value):
        return value
    return "unrecognised"


def _diag_summary(path: Path) -> DiagSummary:
    data = _load_diag(path)
    if data is None:
        return DiagSummary(path.name, False, None, (), 0, ())
    count = data.get("poll_timeouts")
    count = count if isinstance(count, int) and not isinstance(count, bool) else None
    settle = [e for e in _as_list(data.get("poll_settle")) if isinstance(e, dict)]
    how = Counter(_how_label(e.get("how")) for e in settle if isinstance(e.get("how"), str))
    details = tuple(
        _cal_event(path.name, "timeout", e.get("last"), e.get("want"))
        for e in _as_list(data.get("poll_timeout_details"))
        if isinstance(e, dict)
    )
    return DiagSummary(
        path.name,
        True,
        count,
        tuple(sorted(how.items())),
        sum(isinstance(e.get("bits"), dict) for e in settle),
        details,
    )


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else []


@dataclass(frozen=True)
class ColdMove:
    source: str
    last: str
    want: str


@dataclass(frozen=True)
class ColdFailure:
    operation: str
    phase: str
    writes: int
    pulses: int


@dataclass(frozen=True)
class ColdReport:
    moves: tuple[ColdMove, ...]
    failure: ColdFailure | None
    homing: str | None
    resync: str | None
    outcome: str | None


def _cold_report(folder: Path) -> ColdReport:
    moves: list[ColdMove] = []
    failure: ColdFailure | None = None
    homing = resync = outcome = None
    for path in sorted(folder.glob("eject*.log")):
        for line in (_read_text(path) or "").splitlines():
            if m := _EJECT_TIMEOUT_RE.search(line):
                moves.append(ColdMove(path.name, _shown(m.group(1)), _shown(m.group(2))))
            if m := _HOMING_RE.search(line):
                homing = f"homing round {m.group(1)}/{m.group(2)}"
            if m := _RESYNC_RE.search(line):
                resync = f"resync status word {_shown(m.group(1))} (want {_shown(m.group(2))})"
            if (m := _FAILED_RE.search(line)) and m.group(1) == "cold_init" and failure is None:
                failure = ColdFailure(m.group(1), m.group(2), int(m.group(3)), int(m.group(4)))
            if m := _OUTCOME_RE.search(line):
                outcome = m.group(1).lower()
    return ColdReport(tuple(moves), failure, homing, resync, outcome)


def _cal_section(events: list[CalEvent], diags: list[DiagSummary]) -> tuple[list[str], str]:
    """Report lines and the verdict fragment for calibration read-back timeouts."""
    log_timeouts = [e for e in events if e.kind == "timeout"]
    mismatches = [e for e in events if e.kind == "mismatch"]
    diag_timeouts = [e for d in diags for e in d.details]
    lines = [f"Calibration read-back poll timeouts: {len(log_timeouts)} timeout(s) in logs, "
             f"{len(diag_timeouts)} in diagnostics, {len(mismatches)} read-back mismatch(es) "
             f"(a mismatch is not a timeout)"]  # fmt: skip
    rows = log_timeouts + diag_timeouts + mismatches
    if rows:
        lines.append(f"  {'source':<22} {'kind':<9} {'last':<10} {'want':<10} class / last bits")
    for e in rows:
        lines.append(
            f"  {e.source:<22} {e.kind:<9} {e.last:<10} {e.want:<10} {e.cls} / {_bits_text(e.last)}"
        )
    for d in diags:
        lines.append("  " + _diag_line(d))
    undetailed = sum(
        d.poll_timeouts for d in diags if not d.details and d.poll_timeouts and d.poll_timeouts > 0
    )
    if not (log_timeouts or diag_timeouts or undetailed):
        lines.append("  no calibration poll timeouts found")
        return lines, "no calibration poll timeouts found"
    # The two sources can describe the same timeouts; report each, never a merged total.
    parts = [
        f"{label} {len(found)} ({_class_counts(found)})"
        for label, found in (("logs", log_timeouts), ("diagnostics", diag_timeouts))
        if found
    ]
    if undetailed:
        parts.append(f"diagnostics count {undetailed} without detail (cannot classify)")
    return lines, f"calibration timeouts: {'; '.join(parts)}"


def _class_counts(events: list[CalEvent]) -> str:
    counts = Counter(e.cls for e in events)
    return ", ".join(f"{n} {cls}" for cls, n in sorted(counts.items()))


def _diag_line(d: DiagSummary) -> str:
    if not d.readable:
        return f"{d.name}: unreadable or not a JSON object"
    how = ", ".join(f"{k} {n}" for k, n in d.settle_how) or "none"
    timeouts = "unreadable" if d.poll_timeouts is None else str(d.poll_timeouts)
    return (
        f"{d.name}: poll_timeouts={timeouts}, settle entries: {how}, {d.with_bits} with bits detail"
    )


def _cold_section(cold: ColdReport) -> tuple[list[str], str]:
    lines = [f"Cold-start move timeouts: {len(cold.moves)} timeout(s)"]
    if not cold.moves and cold.failure is None:
        lines.append("  no cold-start move timeouts found")
        return lines, "no cold-start move timeouts found"
    for move in cold.moves:
        lines.append(
            f"  {move.source}: last {move.last} ({_bits_text(move.last)}), "
            f"want {move.want} ({_bits_text(move.want)})"
        )
    if cold.homing:
        lines.append(f"  last {cold.homing}")
    if cold.resync:
        lines.append(f"  {cold.resync}")
    if cold.failure:
        f = cold.failure
        lines.append(
            f"  failed in {f.operation}, phase {f.phase}, after {f.writes} writes "
            f"({f.pulses} execute pulses)"
        )
    note = f"outcome: {cold.outcome}" if cold.outcome else NO_HISTORY_NOTE
    lines.append(f"  {note}")
    return lines, f"cold-start move: {len(cold.moves)} timeout(s), {note}"


def _run_lines(folder: Path) -> list[str]:
    diags = [_diag_summary(p) for p in sorted(folder.glob("*.diag.json"))]
    cal_lines, cal_verdict = _cal_section(_log_cal_events(folder), diags)
    cold_lines, cold_verdict = _cold_section(_cold_report(folder))
    name = folder.name or str(folder)
    return [
        f"== run: {name} ==",
        *cal_lines,
        *cold_lines,
        f"VERDICT {name}: {cal_verdict}; {cold_verdict}",
    ]


def build_report(folders: Sequence[Path]) -> str:
    """The full text report; raises OSError only for an unreadable folder."""
    blocks = [HEADER]
    for folder in folders:
        blocks.append("\n".join(_run_lines(Path(folder))))
    return "\n\n".join(blocks) + "\n"


def _readable_dir(folder: Path) -> bool:
    try:
        if not folder.is_dir():
            return False
        next(folder.iterdir(), None)  # raises OSError when the folder cannot be listed
        return True
    except OSError:
        return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline poll-timeout report for run folders.")
    parser.add_argument("folder", type=Path, nargs="+")
    args = parser.parse_args(argv)
    readable = [f for f in args.folder if _readable_dir(f)]
    for bad in (f for f in args.folder if f not in readable):
        sys.stderr.write(f"error: cannot read folder {bad}\n")
    report = build_report(readable) if readable else HEADER + "\n"
    sys.stdout.write(report.encode("ascii", "backslashreplace").decode("ascii"))
    return 0 if len(readable) == len(args.folder) else 2


if __name__ == "__main__":
    sys.exit(main())
