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
        min_scratch_free_gb=0.0,
        scratch_dir=tmp_path / "scratch",
        scanner_locks=(tmp_path / "scanner.lock",),
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
        (0, "reg 0x01 = 0x22\npresent: True\nstatus word: 0x2200\n", pf.WARN, "not loaded"),
        (1, LOADED, pf.WARN, "exited 1"),
        (1, UNLOADED, pf.WARN, "not loaded"),
        (0, "reg 0x01 = 0x22\n", pf.WARN, "UNKNOWN"),
        (0, "reg 0x01 = 0x00\n", pf.FAIL, "cold start"),
        (0, "reg 0x01 = 0x40\n", pf.FAIL, "not idle-homed"),
        (1, "error: No device found", pf.FAIL, "not connected"),
        (0, "garbage", pf.FAIL, "UNKNOWN"),
        (1, "", pf.FAIL, "UNKNOWN"),
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
    res = pf.check_scanner(ctx)
    assert res.status == pf.FAIL and "UNKNOWN" in res.detail
    ctx.run = FakeRun({"doctor": OSError("boom")})
    assert pf.check_scanner(ctx).status == pf.FAIL
    ctx.run = FakeRun({"doctor": cp(LOADED)})
    assert pf.check_scanner(ctx).status == pf.PASS


def test_watcher_unreachable_bus_hint(ctx):
    ctx.run = FakeRun({pf.WATCH_UNIT: cp("", 1, "Failed to connect to bus")})
    res = pf.check_watcher(ctx)
    assert res.status == pf.WARN
    assert "Failed to connect to bus" in res.detail and "flatpak-spawn --host" in res.detail
    ctx.run = FakeRun({pf.WATCH_UNIT: FileNotFoundError("systemctl")})
    assert "flatpak-spawn --host" in pf.check_watcher(ctx).detail


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["python3", "/usr/bin/opticfilm"], True),
        (["/usr/bin/python3.12", "-u", "/home/u/.local/bin/opticfilm", "--x"], True),
        (["/usr/bin/opticfilm"], True),
        (["python3", "-m", "of135i", "watch"], True),
        (["python3", "-m", "opticfilm_linux.app.qt.app"], True),
        (["python3", "-c", "from opticfilm_linux.app.qt.app import main"], True),
        (["python3", "/x/ofscan", "batch", "run"], True),
        (["less", "/x/of135i"], False),
        (["grep", "opticfilm_linux.app", "log"], False),
        (["python3", "-m", "pytest", "tests/of135i"], False),
        (["python3", "script.py", "opticfilm"], False),
        (["python3", "-c", "print('of135i')"], False),
        (["vim", "opticfilm"], False),
        ([], False),
    ],
)
def test_is_scanner_user(argv, expected):
    assert pf.is_scanner_user(argv) is expected


def test_process_opticfilm_script(ctx):
    add_proc(ctx, "300", "/usr/bin/python3", "/home/u/.local/bin/opticfilm")
    res = pf.check_processes(ctx)
    assert res.status == pf.FAIL and "300" in res.detail


def test_scanner_lock_probe(tmp_path):
    lock = tmp_path / "scanner.lock"
    assert pf.probe_lock_held(lock) is False  # absent: not created
    assert not lock.exists()
    lock.write_text("pid 1\n")
    before = lock.stat()
    assert pf.probe_lock_held(lock) is False
    with open(lock) as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        assert pf.probe_lock_held(lock) is True
    assert pf.probe_lock_held(lock) is False  # our probe left no lock behind
    assert lock.read_text() == "pid 1\n" and lock.stat().st_mtime_ns == before.st_mtime_ns


def test_scanner_lock_check(ctx, tmp_path):
    a, b = tmp_path / "a.lock", tmp_path / "b.lock"
    ctx.scanner_locks = (a, b)
    assert pf.check_scanner_lock(ctx).status == pf.PASS
    b.write_text("")
    with open(b) as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        res = pf.check_scanner_lock(ctx)
    assert res.status == pf.FAIL and "held by another process" in res.detail
    link = tmp_path / "link.lock"
    link.symlink_to(b)  # never followed, so it cannot be tested
    ctx.scanner_locks = (link,)
    assert pf.check_scanner_lock(ctx).status == pf.WARN


def test_lock_paths_follow_environment(tmp_path):
    env = {"XDG_STATE_HOME": str(tmp_path), "OF135I_LOCK_FILE": str(tmp_path / "d.lock")}
    assert pf.scanner_lock_paths(env) == (
        tmp_path / "opticfilm-linux" / "scanner.lock",
        tmp_path / "d.lock",
    )
    default = pf.scanner_lock_paths({"XDG_STATE_HOME": "relative"})
    assert default[0].parts[-3:] == ("state", "opticfilm-linux", "scanner.lock")
    assert str(default[1]) == pf.DRIVER_LOCK_DEFAULT


def test_scratch_dir_resolution_and_check(ctx, tmp_path):
    assert pf.app_scratch_dir({"XDG_CACHE_HOME": str(tmp_path)}) == (
        tmp_path / "opticfilm-linux" / "work"
    )
    assert pf.app_scratch_dir({}).parts[-3:] == (".cache", "opticfilm-linux", "work")
    ctx.min_scratch_free_gb = 0.0
    ctx.scratch_dir = tmp_path / "nope" / "work"  # not created: nearest parent is tested
    res = pf.check_disk_scratch(ctx)
    assert res.status == pf.PASS and "not created yet" in res.detail
    ctx.min_scratch_free_gb = 10**9
    assert pf.check_disk_scratch(ctx).status == pf.FAIL


def test_scratch_write_failure(ctx, monkeypatch):
    ctx.min_scratch_free_gb = 0.0
    monkeypatch.setattr(pf, "write_probe", lambda d: "cannot write+fsync a small file (quota)")
    assert pf.check_disk_scratch(ctx).status == pf.FAIL


def test_skip_scanner_final_line(tmp_path, capsys):
    argv = ["--driver-dir", str(tmp_path), "--dest", str(tmp_path), "--skip-scanner"]
    assert pf.main(argv, fake_checks(pf.PASS)) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "GO (scanner state NOT verified)"
    assert pf.main(argv, fake_checks(pf.FAIL)) == 1
    assert capsys.readouterr().out.splitlines()[-1] == "NO-GO"


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


def test_held_scanner_lock_gives_no_go_end_to_end(ctx):
    lock = ctx.scanner_locks[0]
    lock.write_text("")
    ctx.run = FakeRun({pf.WATCH_UNIT: cp("inactive"), "import of135i": cp(), "doctor": cp(LOADED)})
    with open(lock) as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        results = dict(pf.run_checks(ctx, pf.CHECKS))
    assert results["scanner-lock"].status == pf.FAIL
    assert "disk-scratch" in results and "locks" in results


def test_run_allowed_refuses_other_commands():
    for bad in (["of135i", "scan"], [sys.executable, "-m", "of135i", "scan"], ["rm", "-rf", "/"]):
        with pytest.raises(PermissionError):
            pf.run_allowed(bad, None)


def test_script_has_no_personal_paths():
    text = SCRIPT.read_text()
    assert "/home/" not in text and "/var/home" not in text
