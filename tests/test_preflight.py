"""Tests for scripts/hardware/preflight.py using fakes only (no scanner, USB or systemd)."""

from __future__ import annotations

import fcntl
import importlib.util
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hardware" / "preflight.py"
spec = importlib.util.spec_from_file_location("preflight", SCRIPT)
assert spec and spec.loader
pf = importlib.util.module_from_spec(spec)
sys.modules["preflight"] = pf
spec.loader.exec_module(pf)

LOADED = "reg 0x01 = 0x22\npresent: True\nstatus word: 0xdc00\n"
UNLOADED = "reg 0x01 = 0x22\npresent: False\nstatus word: 0x0000\n"


def cp(out: str = "", rc: int = 0, err: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], rc, out, err)


class FakeRun:
    def __init__(self, answers: Mapping[str, subprocess.CompletedProcess[str] | Exception]):
        self.answers = answers
        self.calls: list[tuple[list[str], Path | None]] = []

    def __call__(self, argv, cwd):
        self.calls.append((list(argv), cwd))
        ans = self.answers[argv[-1]]
        if isinstance(ans, Exception):
            raise ans
        return ans


@pytest.fixture
def ctx(tmp_path: Path):
    (tmp_path / "drv" / "of135i").mkdir(parents=True)
    (tmp_path / "dest").mkdir()
    (tmp_path / "proc").mkdir()
    return pf.Context(
        driver_dir=tmp_path / "drv",
        dest=tmp_path / "dest",
        tmp_dir=tmp_path,
        proc_root=tmp_path / "proc",
        min_free_gb=0.0,
        min_tmp_free_gb=0.0,
        run=FakeRun({}),
    )


def test_disk_pass_fail_and_write_probe(ctx, monkeypatch):
    assert pf.check_disk_dest(ctx).status == pf.PASS
    ctx.min_free_gb = 10**9
    assert pf.check_disk_dest(ctx).status == pf.FAIL
    ctx.min_free_gb = 0.0
    monkeypatch.setattr(pf, "write_probe", lambda d: "cannot write+fsync a small file (quota)")
    res = pf.check_disk_tmp(ctx)
    assert res.status == pf.FAIL and "quota" in res.detail


def test_write_probe_real_failure(tmp_path):
    assert pf.write_probe(tmp_path) is None
    assert pf.write_probe(tmp_path / "missing") is not None
    assert list(tmp_path.iterdir()) == []


def test_disk_missing_dir(ctx):
    ctx.dest = ctx.dest / "nope"
    assert pf.check_disk_dest(ctx).status == pf.FAIL


def test_dest_paths(ctx, tmp_path):
    assert pf.check_dest(ctx).status == pf.PASS
    ctx.dest = tmp_path / "missing"
    assert pf.check_dest(ctx).status == pf.FAIL
    f = tmp_path / "file"
    f.write_text("x")
    ctx.dest = f
    assert pf.check_dest(ctx).status == pf.FAIL


def test_dest_symlink(ctx, tmp_path):
    inside = tmp_path / "dest" / "real"
    inside.mkdir()
    (tmp_path / "dest" / "link").symlink_to(inside)
    ctx.dest = tmp_path / "dest" / "link"
    assert pf.check_dest(ctx).status == pf.PASS
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (tmp_path / "dest" / "out").symlink_to(outside)
    ctx.dest = tmp_path / "dest" / "out"
    assert pf.check_dest(ctx).status == pf.FAIL


@pytest.mark.parametrize(
    ("answer", "status"),
    [
        (cp("inactive\n", 3), pf.PASS),
        (cp("active\n"), pf.FAIL),
        (cp("", 1, "weird"), pf.WARN),
        (FileNotFoundError("systemctl"), pf.WARN),
    ],
)
def test_watcher(ctx, answer, status):
    ctx.run = FakeRun({pf.WATCH_UNIT: answer})
    assert pf.check_watcher(ctx).status == status


def test_driver(ctx):
    ctx.run = FakeRun({"import of135i": cp()})
    assert pf.check_driver(ctx).status == pf.PASS
    assert ctx.run.calls[0][1] == ctx.driver_dir
    ctx.run = FakeRun({"import of135i": cp("", 1, "ImportError: usb")})
    res = pf.check_driver(ctx)
    assert res.status == pf.FAIL and "usb" in res.detail
    ctx.driver_dir = ctx.driver_dir / "x"
    assert pf.check_driver(ctx).status == pf.FAIL


@pytest.mark.parametrize(
    ("rc", "out", "status", "word"),
    [
        (0, LOADED, pf.PASS, "holder loaded"),
        (0, UNLOADED, pf.WARN, "not loaded"),
        (0, "reg 0x01 = 0x22\n", pf.WARN, "UNKNOWN"),
        (0, "reg 0x01 = 0x00\n", pf.FAIL, "cold start"),
        (0, "reg 0x01 = 0x40\n", pf.FAIL, "not idle-homed"),
        (1, "error: No device found", pf.FAIL, "not connected"),
        (0, "garbage", pf.WARN, "UNKNOWN"),
        (1, "", pf.WARN, "UNKNOWN"),
    ],
)
def test_doctor_interpretation(rc, out, status, word):
    res = pf.interpret_doctor(rc, out)
    assert res.status == status and word in res.detail


def test_scanner_skip_and_run_errors(ctx):
    ctx.skip_scanner = True
    ctx.run = FakeRun({})
    res = pf.check_scanner(ctx)
    assert res.status == pf.WARN and ctx.run.calls == []
    ctx.skip_scanner = False
    ctx.run = FakeRun({"doctor": subprocess.TimeoutExpired("x", 1)})
    assert "UNKNOWN" in pf.check_scanner(ctx).detail
    ctx.run = FakeRun({"doctor": cp(LOADED)})
    assert pf.check_scanner(ctx).status == pf.PASS


def add_proc(ctx, pid: str, *args: str):
    d = ctx.proc_root / pid
    d.mkdir()
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in args) + b"\0")


def test_processes(ctx):
    add_proc(ctx, "100", "bash")
    add_proc(ctx, "101", "python3", "preflight.py", "--driver-dir", "of135i-notarg")
    assert pf.check_processes(ctx).status == pf.PASS
    add_proc(ctx, "102", "python3", "-m", "of135i", "watch")
    res = pf.check_processes(ctx)
    assert res.status == pf.FAIL and "102" in res.detail


def test_processes_app_and_self_excluded(ctx):
    add_proc(ctx, "200", "python3", "-c", "from opticfilm_linux.app.qt.app import main")
    ctx.self_pid = 200
    assert pf.check_processes(ctx).status == pf.PASS
    ctx.self_pid = 1
    assert pf.check_processes(ctx).status == pf.FAIL


def test_processes_proc_unreadable(ctx):
    ctx.proc_root = ctx.proc_root / "missing"
    assert pf.check_processes(ctx).status == pf.WARN


def test_locks(ctx):
    assert pf.check_locks(ctx).status == pf.PASS
    lock = ctx.dest / "batch.lock"
    lock.write_text("")
    assert pf.check_locks(ctx).status == pf.PASS  # stale file, nobody holds it
    with open(lock) as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        res = pf.check_locks(ctx)
    assert res.status == pf.FAIL and "batch.lock" in res.detail


def fake_checks(*statuses: str):
    return {f"c{i}": (lambda c, s=s: pf.Result(s, "x")) for i, s in enumerate(statuses)}


@pytest.mark.parametrize(
    ("statuses", "final", "code"),
    [
        ((pf.PASS, pf.PASS), "GO", 0),
        ((pf.PASS, pf.WARN), "GO", 0),
        ((pf.PASS, pf.WARN, pf.FAIL), "NO-GO", 1),
    ],
)
def test_main_aggregation(tmp_path, capsys, statuses, final, code):
    argv = ["--driver-dir", str(tmp_path), "--dest", str(tmp_path)]
    assert pf.main(argv, fake_checks(*statuses)) == code
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == final
    assert [ln[:4] for ln in lines[:-1]] == list(statuses)


def test_crashing_check_is_fail(ctx):
    def boom(c):
        raise RuntimeError("bad")

    results = pf.run_checks(ctx, {"boom": boom})
    assert results[0][1].status == pf.FAIL and pf.verdict(results) == "NO-GO"


def test_only_allow_listed_commands(ctx):
    answers = {pf.WATCH_UNIT: cp("inactive"), "import of135i": cp(), "doctor": cp(LOADED)}
    fake = FakeRun(answers)
    ctx.run = fake
    results = pf.run_checks(ctx, pf.CHECKS)
    assert pf.verdict(results) == "GO", results
    assert [c[0] for c in fake.calls] == [list(a) for a in pf.allowed_argvs()]
    assert [a[1:] for a in pf.allowed_argvs()[1:]] == [
        ("-c", "import of135i"),
        ("-m", "of135i", "doctor"),
    ]


def test_run_allowed_refuses_other_commands():
    for bad in (["of135i", "scan"], [sys.executable, "-m", "of135i", "scan"], ["rm", "-rf", "/"]):
        with pytest.raises(PermissionError):
            pf.run_allowed(bad, None)


def test_script_has_no_personal_paths():
    text = SCRIPT.read_text()
    assert "/home/" not in text and "/var/home" not in text
