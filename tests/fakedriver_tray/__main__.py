"""`python -m fakedriver_tray load|scan|eject`; behaviour switched by env vars.

FAKE_CALL_LOG  file to append one line per call (command + argv)
FAKE_LOAD_FAIL exit code for load
FAKE_MODE      ok | exit4 | poll | crash | nosidecar | nocrop
FAKE_SCAN_DELAY  seconds to sleep before scan writes output (default 0)
FAKE_NEXT_STRIP_FAIL exit code for `load --next-strip`
FAKE_EJECT_FAIL  exit code for eject
FAKE_SIGINT_FILE file to append '<cmd> sigint_ignored=<bool>' per call
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path


def _log(argv: list[str]) -> None:
    path = os.environ.get("FAKE_CALL_LOG")
    if path:
        with open(path, "a") as fh:
            fh.write(" ".join(argv) + "\n")


def _sidecar(n: int, verified: bool, polls: int) -> dict:
    return {
        "coverage": {"verified": verified, "method": "slide-ir"},
        "feedl": 6000 + n,
        "phase_seconds": {"park": 3.7, "scan": 43.8},
        "poll_timeouts": polls,
        "total_seconds": 54.6,
    }


def _write_opening(stem: Path, n: int, mode: str) -> None:
    base = f"{stem.stem}-f{n}"
    d = stem.parent
    verified = not (mode == "exit4" and n == 2)
    polls = 2 if mode == "poll" and n == 3 else 0
    for name in (f"{base}.overscan.tiff", f"{base}-ir.overscan.tiff"):
        (d / name).write_bytes(b"x")
    if verified and not (mode == "nocrop" and n == 1):
        (d / f"{base}.tiff").write_bytes(b"x")
        (d / f"{base}-ir.tiff").write_bytes(b"x")
    if mode != "nosidecar" or n != 4:
        (d / f"{base}.diag.json").write_text(json.dumps(_sidecar(n, verified, polls)))
    sys.stderr.write(f"dual slide {n} (3600 dpi): FEEDL={6000 + n} chunks=1\n")


def _frames(argv: list[str]) -> list[int]:
    """Expand `--frames 1-3,4` (default 1-4)."""
    spec = argv[argv.index("--frames") + 1] if "--frames" in argv else "1-4"
    out: list[int] = []
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        out += range(int(lo), int(hi or lo) + 1)
    return out


def _scan(argv: list[str]) -> int:
    mode = os.environ.get("FAKE_MODE", "ok")
    time.sleep(float(os.environ.get("FAKE_SCAN_DELAY", "0")))
    if mode == "crash":
        sys.stderr.write("Traceback: boom\n")
        return 1
    stem = Path(argv[argv.index("-o") + 1])
    for n in _frames(argv):
        _write_opening(stem, n, mode)
    return 4 if mode == "exit4" else 0


def main() -> int:
    argv = sys.argv[1:]
    _log(argv)
    cmd = argv[0]
    path = os.environ.get("FAKE_SIGINT_FILE")
    if path:
        ignored = signal.getsignal(signal.SIGINT) == signal.SIG_IGN
        with open(path, "a") as fh:
            fh.write(f"{cmd} sigint_ignored={ignored}\n")
    if cmd == "load" and "--next-strip" in argv:
        return int(os.environ.get("FAKE_NEXT_STRIP_FAIL", "0"))
    if cmd == "load":
        return int(os.environ.get("FAKE_LOAD_FAIL", "0"))
    if cmd == "scan":
        return _scan(argv)
    return int(os.environ.get("FAKE_EJECT_FAIL", "0"))


if __name__ == "__main__":
    sys.exit(main())
