"""Tests for scripts/hardware/scan_slide_tray.py using a fake driver (no scanner)."""

from __future__ import annotations

import csv
import importlib.util
import os
import signal
import subprocess
import sys
import threading
from collections import namedtuple
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "hardware" / "scan_slide_tray.py"
_spec = importlib.util.spec_from_file_location("scan_slide_tray", SCRIPT)
sst = importlib.util.module_from_spec(_spec)
sys.modules["scan_slide_tray"] = sst
_spec.loader.exec_module(sst)

TESTS = Path(__file__).resolve().parent
Usage = namedtuple("Usage", "total used free")


@pytest.fixture
def env(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_CALL_LOG", str(log))
    monkeypatch.delenv("FAKE_MODE", raising=False)
    monkeypatch.delenv("FAKE_LOAD_FAIL", raising=False)
    monkeypatch.setattr(sst.shutil, "disk_usage", lambda p: Usage(10**13, 0, 10**13))
    return out, log


def run(out, *extra, answers=("",), mode=None):
    if mode:
        os.environ["FAKE_MODE"] = mode
    it = iter(answers)

    def fake_input(_prompt: str) -> str:
        return next(it, "q")

    argv = [
        "--driver-dir",
        str(TESTS),
        "--out-root",
        str(out),
        "--python",
        sys.executable,
        "--module",
        "fakedriver_tray",
        *extra,
    ]
    return sst.main(argv, input_fn=fake_input)


@pytest.fixture(autouse=True)
def _clear_mode():
    yield
    os.environ.pop("FAKE_MODE", None)


def rows(out):
    with (out / "tray_log.csv").open() as fh:
        return list(csv.DictReader(fh))


def verbs(log):
    return [c.split()[0] for c in log.read_text().splitlines()] if log.exists() else []


def test_ok_path_keeps_overscan_by_default(env):
    out, log = env
    assert run(out, "--max-trays", "1") == 0
    d = out / "tray0001"
    assert len(list(d.glob("*.overscan.tiff"))) == 8
    assert (d / "scan.log").read_text().count("FEEDL=") == 4
    assert verbs(log) == ["load", "scan", "eject"]
    scan = log.read_text().splitlines()[1]
    assert "--holder slide --frames 1-4 --dpi 3600 --ir --slide-positive" in scan
    assert "--overscan" not in scan
    assert scan.endswith(str(d / "tray0001.tiff"))


def test_csv_columns_and_values(env):
    out, _ = env
    run(out, "--max-trays", "1", "--overscan", "1.5")
    r = rows(out)
    assert len(r) == 4
    assert tuple(r[0].keys()) == sst.CSV_COLUMNS
    assert [x["opening"] for x in r] == ["1", "2", "3", "4"]
    assert r[0]["status"] == "OK" and r[0]["verified"] == "true"
    assert r[0]["tray"] == "1" and r[0]["exit_code"] == "0"
    assert r[0]["poll_timeouts"] == "0" and r[0]["feedl"] == "6001"
    assert r[0]["scan_seconds"] == "43.800" and r[0]["total_seconds"] == "54.600"
    assert r[0]["wall_seconds"] and r[0]["overscan_dropped"] == "false"
    assert r[0]["cropped_path"].endswith("tray0001-f1.tiff")


def test_overscan_passed_through(env):
    out, log = env
    run(out, "--max-trays", "1", "--overscan", "1.5")
    assert "--overscan 1.5" in log.read_text().splitlines()[1]


def test_drop_overscan_on_verified(env):
    out, _ = env
    run(out, "--max-trays", "1", "--drop-overscan")
    d = out / "tray0001"
    assert not list(d.glob("*.overscan.tiff"))
    assert (d / "tray0001-f1.tiff").exists() and (d / "tray0001-f1-ir.tiff").exists()
    assert all(x["overscan_dropped"] == "true" for x in rows(out))


def test_drop_skipped_when_cropped_missing(env):
    out, _ = env
    # opening 1 has no cropped files: its overscan stays, others' go
    run(out, "--max-trays", "1", "--drop-overscan", mode="nocrop")
    d = out / "tray0001"
    assert (d / "tray0001-f1.overscan.tiff").exists()
    assert (d / "tray0001-f1-ir.overscan.tiff").exists()
    assert not (d / "tray0001-f2.overscan.tiff").exists()
    assert rows(out)[0]["overscan_dropped"] == "false"
    assert rows(out)[1]["overscan_dropped"] == "true"


def test_drop_skipped_on_empty_cropped(tmp_path):
    stem = tmp_path / "t"
    paths = sst.frame_paths(stem, 1)
    for k in ("vis", "ir_over", "vis_over"):
        paths[k].write_bytes(b"x")
    paths["ir"].write_bytes(b"")
    op = sst.Opening(tray=1, opening=1, status="OK", exit_code=0, verified=True)
    out = sst.drop_overscan_frames(op, stem)
    assert out.overscan_dropped is False
    assert paths["vis_over"].exists() and paths["ir_over"].exists()


@pytest.mark.parametrize(
    "kw",
    [
        {"status": "UNVERIFIED"},
        {"exit_code": 4},
        {"verified": False},
        {"verified": None},
    ],
)
def test_drop_never_when_not_verified(tmp_path, kw):
    stem = tmp_path / "t"
    paths = sst.frame_paths(stem, 1)
    for k in ("vis", "ir", "ir_over", "vis_over"):
        paths[k].write_bytes(b"x")
    base = {"tray": 1, "opening": 1, "status": "OK", "exit_code": 0, "verified": True}
    sst.drop_overscan_frames(sst.Opening(**{**base, **kw}), stem)
    assert paths["vis_over"].exists() and paths["ir_over"].exists()


def test_unverified_stops_and_keeps_overscan(env):
    out, log = env
    assert run(out, "--drop-overscan", answers=("", ""), mode="exit4") == 1
    assert len(list((out / "tray0001").glob("*.overscan.tiff"))) == 8
    assert "eject" not in verbs(log) and verbs(log).count("load") == 1
    st = {x["opening"]: x["status"] for x in rows(out)}
    assert st["2"] == "UNVERIFIED"
    assert rows(out)[1]["exit_code"] == "4" and rows(out)[1]["verified"] == "false"


def test_unverified_continue(env):
    out, log = env
    rc = run(
        out,
        "--keep-going-on-unverified",
        "--drop-overscan",
        "--max-trays",
        "2",
        answers=("", ""),
        mode="exit4",
    )
    assert rc == 0
    assert verbs(log).count("eject") == 2
    assert len(list((out / "tray0001").glob("*.overscan.tiff"))) == 8  # exit 4 drops nothing
    assert all(x["overscan_dropped"] == "false" for x in rows(out))
    assert {x["tray"] for x in rows(out)} == {"1", "2"}
    assert sst.next_tray_from_csv(out / "tray_log.csv") == 3


def test_poll_timeout_stops_without_eject(env):
    out, log = env
    assert run(out, "--keep-going-on-unverified", "--drop-overscan", mode="poll") == 1
    assert "eject" not in verbs(log)
    assert rows(out)[2]["status"] == "POLL_TIMEOUT"
    assert rows(out)[2]["poll_timeouts"] == "2"
    assert len(list((out / "tray0001").glob("*.overscan.tiff"))) == 8


def test_crash_stops(env):
    out, log = env
    assert run(out, mode="crash") == 1
    assert "eject" not in verbs(log)
    assert {x["status"] for x in rows(out)} == {"SCAN_FAILED"}


def test_missing_sidecar_stops(env):
    out, log = env
    assert run(out, mode="nosidecar") == 1
    assert rows(out)[3]["status"] == "NO_SIDECAR"
    assert "eject" not in verbs(log)


def test_load_failure_aborts(env, monkeypatch):
    out, log = env
    monkeypatch.setenv("FAKE_LOAD_FAIL", "3")
    assert run(out) == 2
    assert verbs(log) == ["load"]


def test_resume_numbering_from_csv(env):
    out, _ = env
    run(out, "--max-trays", "2", answers=("", ""))
    run(out, "--max-trays", "1")
    assert (out / "tray0003").is_dir()
    assert sst.next_tray_from_csv(out / "tray_log.csv") == 4


def test_failed_tray_not_counted_and_dir_not_overwritten(env):
    out, _ = env
    run(out, mode="crash")
    assert sst.next_tray_from_csv(out / "tray_log.csv") == 1
    os.environ.pop("FAKE_MODE")
    assert run(out) == 2  # tray0001 exists: refuse to overwrite


def test_start_tray_explicit(env):
    out, _ = env
    run(out, "--max-trays", "1", "--start-tray", "17")
    assert (out / "tray0017").is_dir()


def test_quit_at_prompt(env):
    out, log = env
    assert run(out, answers=("q",)) == 0
    assert verbs(log) == []


def test_low_disk_refused(env, monkeypatch):
    out, log = env
    monkeypatch.setattr(sst.shutil, "disk_usage", lambda p: Usage(10**13, 0, 4 * 1024**3))
    assert run(out) == 2
    assert verbs(log) == []


def test_bad_args_refused(env):
    out, log = env
    assert run(out, "--dpi", "123") == 2
    assert run(out, "--overscan", "-1") == 2
    assert run(out.parent / "missing") == 2
    assert verbs(log) == []


def test_no_shell_used(env, monkeypatch):
    out, _ = env
    seen = []
    real = subprocess.Popen

    class Spy(real):
        def __init__(self, args, **kw):
            seen.append((args, kw))
            super().__init__(args, **kw)

    monkeypatch.setattr(sst.subprocess, "Popen", Spy)
    run(out, "--max-trays", "1")
    assert len(seen) == 3
    for args, kw in seen:
        assert isinstance(args, list) and not kw.get("shell")
        assert kw["cwd"] == TESTS
    assert "shell=True" not in SCRIPT.read_text()


def test_interrupt_waits_for_child(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "sleep")
    monkeypatch.setenv("FAKE_CALL_LOG", str(tmp_path / "c.log"))
    timer = threading.Timer(1.0, os.kill, (os.getpid(), signal.SIGINT))
    timer.start()
    try:
        with pytest.raises(sst.ChildInterrupted) as ei:
            sst.run_child(
                [sys.executable, "-m", "fakedriver_tray", "scan", "-o", str(tmp_path / "x.tiff")],
                TESTS,
                grace=0.3,
            )
    finally:
        timer.cancel()
    assert ei.value.returncode == 130  # child handled the single SIGINT, was not killed


def test_main_reports_tray_in_flight(env, monkeypatch, caplog):
    out, _ = env

    def boom(*a, **k):
        raise sst.ChildInterrupted(130)

    monkeypatch.setattr(sst, "run_child", boom)
    assert run(out) == 130
    assert "tray 1 was in flight" in caplog.text
