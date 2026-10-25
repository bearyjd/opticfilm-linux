"""One program on the scanner at a time, and a driver that always ends when its caller does."""

import fcntl
import signal
from pathlib import Path

import pytest

from opticfilm_linux.scanner import driver as driver_module
from opticfilm_linux.scanner.base import ScanError, ScannerStatus
from opticfilm_linux.scanner.driver import DriverConfig, DriverScanner

FAKE_DRIVER = Path(__file__).parent / "fake_driver"


@pytest.fixture
def scanner(sweep600_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(driver_module, "LOCK_WAIT_S", 0.3)  # a refusal need not take 22 s here
    monkeypatch.setenv("FAKE_DRIVER_SWEEP", str(sweep600_dir))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("FAKE_DRIVER_TRACE", str(tmp_path / "trace.log"))
    return DriverScanner(DriverConfig(driver_dir=FAKE_DRIVER, log_dir=tmp_path / "logs"))


@pytest.fixture
def other_program(tmp_path):
    """Holds the scanner's lock file the way a second ofscan or app process would."""
    path = tmp_path / "state" / "opticfilm-linux" / "scanner.lock"
    path.parent.mkdir(parents=True)
    handle = open(path, "w")
    fcntl.flock(handle, fcntl.LOCK_EX)
    yield handle
    handle.close()


def test_eject_is_refused_while_another_program_holds_the_scanner(scanner, other_program, tmp_path):
    with pytest.raises(ScanError, match="another program"):
        scanner.eject()
    assert not (tmp_path / "trace.log").exists()  # the driver was never started
    assert not scanner._needs_power_cycle  # a refusal is not a scanner failure


def test_a_scan_is_refused_while_another_program_holds_the_scanner(scanner, other_program):
    from opticfilm_linux.model import ScanSettings

    with pytest.raises(ScanError, match="another program"):
        scanner.scan_holder(ScanSettings(dpi=600))


def test_status_reads_busy_while_another_program_holds_the_scanner(scanner, other_program):
    assert scanner.status() is ScannerStatus.BUSY


def test_the_lock_is_free_again_after_an_eject_and_after_a_driver_failure(
    scanner, tmp_path, monkeypatch
):
    path = tmp_path / "state" / "opticfilm-linux" / "scanner.lock"

    def free() -> bool:
        with open(path, "w") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            return True

    scanner.eject()
    assert free()

    def failing_run(*_args, **_kwargs):
        raise ScanError("the driver did not finish")

    monkeypatch.setattr(scanner, "_run", failing_run)
    with pytest.raises(ScanError):
        scanner.eject()
    assert free()


def test_an_unwritable_lock_location_does_not_stop_a_single_program(
    scanner, tmp_path, monkeypatch, caplog
):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    monkeypatch.setenv("XDG_STATE_HOME", str(blocker))
    scanner.eject()  # works on the in-process lock alone
    assert "lock" in caplog.text.lower()


def test_a_second_ctrl_c_while_stopping_the_driver_still_ends_it(monkeypatch):
    sent: list[int] = []

    class Proc:
        pid = 4242
        waits = 0
        finished = False

        def wait(self, timeout=None):
            self.waits += 1
            if self.waits == 1:
                raise KeyboardInterrupt  # the impatient second Ctrl-C
            self.finished = True
            return 0

    monkeypatch.setattr(driver_module.os, "killpg", lambda pid, sig: sent.append(sig))
    proc = Proc()
    driver_module._stop_group(proc)  # type: ignore[arg-type]
    assert proc.finished  # kept waiting for the driver to fail closed
    assert sent[0] == signal.SIGINT and sent[-1] == signal.SIGKILL


def test_a_short_status_check_by_another_program_is_waited_out(scanner, other_program):
    import threading

    threading.Timer(0.1, other_program.close).start()  # the other program lets go
    scanner.eject()  # waits for it, then runs


def test_a_refusal_is_not_given_power_cycle_advice_by_the_cli(
    scanner, other_program, monkeypatch, capsys
):
    from opticfilm_linux.cli import main

    monkeypatch.setattr("opticfilm_linux.batch.cli.real_scanner", lambda: scanner)
    assert main(["batch", "eject"]) == 2
    err = capsys.readouterr().err
    assert "another program" in err and "power-cycle" not in err.lower()
