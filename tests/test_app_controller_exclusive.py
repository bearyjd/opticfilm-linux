"""A scan and a holder movement never overlap or run late: one reservation covers both."""

import threading
from pathlib import Path

from controller_helpers import open_new, wait_until
from opticfilm_linux.scanner.base import ScannerStatus
from test_app_controller_holder import build  # noqa: F401 - the fixture

WAIT_SCAN = "Wait for the scan to finish"
WAIT_HOLDER = "Wait for the eject/load to finish"
WAIT_EXPORT = "Wait for the export to finish, then try again"


def _block(pool) -> threading.Event:
    """Hold a worker, so whatever is submitted next stays queued."""
    gate, started = threading.Event(), threading.Event()
    pool.submit(lambda: (started.set(), gate.wait(30)))
    assert started.wait(10)
    return gate


def _banner(c) -> str:
    wait_until(c, lambda: c.state().banner is not None)
    return c.state().banner.message


def test_eject_is_refused_while_a_scan_is_queued(build, tmp_path):  # noqa: F811
    c, scanner = build(status=ScannerStatus.READY)
    open_new(c, tmp_path / "b")
    wait_until(c, lambda: c.state().can_scan)
    gate = _block(c._command)
    c.scan()  # queued behind the blocked command worker, not yet handed to the runner
    c.eject_holder()
    c.load_next_holder()
    assert WAIT_SCAN in _banner(c)
    gate.set()
    wait_until(c, lambda: len(c.state().trays) == 1)
    assert scanner.calls == []


def test_scan_is_refused_while_the_holder_moves(build, tmp_path):  # noqa: F811
    gate = threading.Event()
    c, scanner = build(status=ScannerStatus.READY, gate=gate)
    open_new(c, tmp_path / "b")
    wait_until(c, lambda: c.state().can_scan)
    c.eject_holder()
    wait_until(c, lambda: scanner.calls == ["eject"])
    wait_until(c, lambda: c.state().holder_busy and not c.state().can_scan)
    c.scan()
    c.rescan(1)
    assert WAIT_HOLDER in _banner(c)
    gate.set()
    wait_until(c, lambda: not c.state().holder_busy and c.state().can_scan)
    assert c.state().trays == ()


def test_eject_during_an_export_is_refused_at_once(build, tmp_path):  # noqa: F811
    c, scanner = build()
    open_new(c, tmp_path / "b")
    wait_until(c, lambda: c.state().can_eject)
    gate = threading.Event()
    c._run(lambda: gate.wait(30), "export stand-in", pool=c._jobs)  # an export running
    c.eject_holder()
    assert WAIT_EXPORT in _banner(c)
    gate.set()
    assert scanner.calls == [] and not c._holder_busy


def test_load_next_during_an_export_does_not_tell_the_owner_to_press_eject(build, tmp_path):  # noqa: F811
    c, scanner = build(next_strip=True)
    open_new(c, tmp_path / "b")
    wait_until(c, lambda: c.state().can_eject)
    gate = threading.Event()
    c._run(lambda: gate.wait(30), "export stand-in", pool=c._jobs)
    c.load_next_holder()
    banner = _banner(c)
    gate.set()
    assert "Eject" not in banner and "export" in banner
    assert scanner.calls == []


def test_eject_behind_a_queued_export_is_refused(build, tmp_path):  # noqa: F811
    c, scanner = build()
    open_new(c, tmp_path / "b")
    wait_until(c, lambda: c.state().can_eject)
    gate = _block(c._command)
    c.export(Path(tmp_path / "out"), "tiff16")  # queued behind the command worker
    c.eject_holder()
    assert WAIT_EXPORT in _banner(c)
    gate.set()
    assert scanner.calls == []


def test_closing_the_batch_cancels_a_holder_command_not_yet_started(build, tmp_path):  # noqa: F811
    c, scanner = build()
    open_new(c, tmp_path / "b")
    wait_until(c, lambda: c.state().can_eject)
    gate = _block(c._holder_pool)
    c.eject_holder()
    c.close_batch()
    gate.set()
    wait_until(c, lambda: c.state().batch_name is None and not c._holder_busy)
    c._holder_pool.submit(lambda: None).result(10)  # anything queued has had its turn
    assert scanner.calls == []
