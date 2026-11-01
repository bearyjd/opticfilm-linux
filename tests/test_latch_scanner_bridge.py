"""Latch observations use existing driver output, never a new hardware request."""

import ast
from pathlib import Path

import pytest

from opticfilm_linux.app.state import EMPTY_STATE
from opticfilm_linux.scanner.driver import DriverConfig, DriverScanner, _Result


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("ejected\n", "ejected"),
        ("ejected (eject move completed: status word read idle)\n", "ejected"),
        ("INFO: not ejected\n", None),
        ("idle\n", None),
        ("eject skipped\n", None),
        ("ejected early\n", None),
        ("ejected (failed)\n", None),
        ("ejected (refused)\n", None),
    ],
)
def test_eject_reports_only_confirmed_completion(tmp_path, monkeypatch, output, expected):
    scanner = DriverScanner(DriverConfig(driver_dir=tmp_path))
    monkeypatch.setattr(scanner, "_require_driver", lambda: None)
    monkeypatch.setattr(scanner, "_run", lambda *a, **kw: _Result(0, output, False))
    assert scanner.eject() is None
    assert scanner.eject_outcome == expected


def test_usb_identity_is_read_only_and_follows_existing_status_output(tmp_path, monkeypatch):
    scanner = DriverScanner(
        DriverConfig(driver_dir=Path(__file__).parent / "fake_driver", log_dir=tmp_path)
    )
    assert scanner.usb_address is None
    scanner.status()
    assert scanner.usb_address == (3, 18)
    monkeypatch.setenv("FAKE_DRIVER_ADDR", "19")
    scanner._status_cache = None
    scanner.status()
    assert scanner.usb_address == (3, 19)
    monkeypatch.setattr(scanner, "_run", lambda *a, **kw: pytest.fail("accessor ran driver"))
    assert scanner.usb_address == (3, 19)


def test_bridge_holder_text_property_defaults_to_unknown_without_qt():
    """Evaluate the real property reader without importing unavailable Qt bindings."""
    source = Path("src/opticfilm_linux/app/qt/bridge.py").read_text()
    bridge = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef))
    assignment = next(
        n
        for n in bridge.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "holderLatchText" for t in n.targets)
    )
    reader = eval(compile(ast.Expression(assignment.value.args[2]), "bridge", "eval"))
    assert reader(EMPTY_STATE, None) == (
        "Holder: unknown after power-on or a fault; check by hand before scanning"
    )


@pytest.mark.parametrize(
    "bus,address",
    [
        ("0", "0"),
        ("255", "255"),
        ("256", "1"),
        ("1", "256"),
        ("0001", "1"),
        ("-1", "1"),
        ("1", "-1"),
        ("1.0", "1"),
        ("1", "1.0"),
        ("9" * 5000, "1"),
        ("1", "9" * 5000),
    ],
)
def test_usb_identity_is_bounded_and_does_not_change_run(tmp_path, monkeypatch, bus, address):
    from opticfilm_linux.scanner import driver

    output = f"opened device bus={bus} addr={address}\n"

    class Process:
        def __init__(self, *args, stdout, **kwargs):
            stdout.write(output.encode())

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(driver.subprocess, "Popen", Process)
    scanner = DriverScanner(DriverConfig(driver_dir=tmp_path))
    result = scanner._run(["doctor"], 1, False)
    assert result == _Result(0, output, False)
    expected = (int(bus), int(address)) if (bus, address) in [("0", "0"), ("255", "255")] else None
    assert scanner.usb_address == expected


def test_run_keeps_original_read_count(tmp_path, monkeypatch):
    import builtins

    from opticfilm_linux.scanner import driver

    reads = []
    original_open = builtins.open

    def counted_open(path, mode="r", *args, **kwargs):
        if mode == "rb":
            reads.append(path)
        return original_open(path, mode, *args, **kwargs)

    class Process:
        def __init__(self, *args, stdout, **kwargs):
            stdout.write(b"opened device bus=3 addr=18\n")

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(driver.subprocess, "Popen", Process)
    monkeypatch.setattr(builtins, "open", counted_open)
    scanner = DriverScanner(DriverConfig(driver_dir=tmp_path))
    scanner._run(["doctor"], 1, False)
    assert len(reads) == 2  # existing output tail and failure-recovery address, no new read
    assert scanner.usb_address == (3, 18)
    assert len(reads) == 2


def test_a_failed_or_skipped_eject_clears_previous_confirmation(tmp_path, monkeypatch):
    from opticfilm_linux.scanner.base import ScanError

    scanner = DriverScanner(DriverConfig(driver_dir=tmp_path))
    monkeypatch.setattr(scanner, "_require_driver", lambda: None)
    monkeypatch.setattr(scanner, "_run", lambda *a, **kw: _Result(0, "ejected\n", False))
    assert scanner.eject() is None
    assert scanner.eject_outcome == "ejected"
    monkeypatch.setattr(scanner, "_run", lambda *a, **kw: _Result(1, "ejected\n", False))
    with pytest.raises(ScanError):
        scanner.eject()
    assert scanner.eject_outcome is None


def test_recording_failures_cannot_escape_a_successful_run(tmp_path, monkeypatch):
    from opticfilm_linux.scanner import driver

    class Scanner(DriverScanner):
        def __setattr__(self, name, value):
            if name == "_latch_output":
                raise RuntimeError("broken informational recording")
            super().__setattr__(name, value)

    class Process:
        def __init__(self, *args, stdout, **kwargs):
            stdout.write(b"opened device bus=3 addr=18\n")

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(driver.subprocess, "Popen", Process)
    scanner = Scanner(DriverConfig(driver_dir=tmp_path))
    assert scanner._run(["doctor"], 1, False).returncode == 0


@pytest.mark.parametrize("returncode,stopped", [(1, False), (0, True)])
def test_eject_outcome_requires_success_and_no_interruption(tmp_path, returncode, stopped):
    scanner = DriverScanner(DriverConfig(driver_dir=tmp_path))
    scanner._eject_result = _Result(returncode, "ejected\n", stopped)
    assert scanner.eject_outcome is None


def test_usb_parser_ignores_identity_beyond_bounded_prefix(tmp_path):
    from opticfilm_linux.scanner.driver import HEAD_BYTES

    scanner = DriverScanner(DriverConfig(driver_dir=tmp_path))
    scanner._latch_output = " " * HEAD_BYTES + "bus=1 addr=1\n"
    assert scanner.usb_address is None


def test_accessor_failures_are_unknown(tmp_path):
    scanner = DriverScanner(DriverConfig(driver_dir=tmp_path))
    scanner._latch_output = object()
    scanner._eject_result = object()
    assert scanner.usb_address is None
    assert scanner.eject_outcome is None


def test_eject_recording_failures_do_not_change_return_value(tmp_path, monkeypatch):
    class Scanner(DriverScanner):
        def __setattr__(self, name, value):
            if name == "_eject_result":
                raise RuntimeError("broken informational recording")
            super().__setattr__(name, value)

    scanner = Scanner(DriverConfig(driver_dir=tmp_path))
    monkeypatch.setattr(scanner, "_require_driver", lambda: None)
    monkeypatch.setattr(scanner, "_run", lambda *a, **kw: _Result(0, "ejected\n", False))
    assert scanner.eject() is None
    assert scanner.eject_outcome is None


def test_skipped_eject_does_not_reuse_an_earlier_confirmation(tmp_path, monkeypatch):
    scanner = DriverScanner(DriverConfig(driver_dir=tmp_path))
    monkeypatch.setattr(scanner, "_require_driver", lambda: None)
    monkeypatch.setattr(scanner, "_run", lambda *a, **kw: _Result(0, "ejected\n", False))
    scanner.eject()
    assert scanner.eject_outcome == "ejected"
    monkeypatch.setattr(scanner, "_run", lambda *a, **kw: _Result(0, "eject skipped\n", False))
    scanner.eject()
    assert scanner.eject_outcome is None


def test_refused_eject_clears_previous_confirmation(tmp_path, monkeypatch):
    from opticfilm_linux.scanner.base import ScanError

    scanner = DriverScanner(DriverConfig(driver_dir=tmp_path))
    monkeypatch.setattr(scanner, "_require_driver", lambda: None)
    monkeypatch.setattr(scanner, "_run", lambda *a, **kw: _Result(0, "ejected\n", False))
    scanner.eject()
    assert scanner.eject_outcome == "ejected"

    def refuse():
        raise ScanError("driver unavailable")

    monkeypatch.setattr(scanner, "_require_driver", refuse)
    with pytest.raises(ScanError, match="driver unavailable"):
        scanner.eject()
    assert scanner.eject_outcome is None


def test_confirmation_is_wired_through_bridge_and_qml():
    bridge = Path("src/opticfilm_linux/app/qt/bridge.py").read_text()
    qml = Path("src/opticfilm_linux/app/qml/MainPage.qml").read_text()
    tree = ast.parse(bridge)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    prop = next(
        n
        for n in cls.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "canConfirmHolderLatched" for t in n.targets)
    )
    reader = eval(compile(ast.Expression(prop.value.args[2]), "bridge", "eval"))
    assert reader(EMPTY_STATE, None) is False
    assert "self._controller.confirm_holder_latched()" in bridge
    assert "visible: bridge.holderLatchWarning" in qml
    assert "Reseat and latch the holder before scanning" in qml
    assert "visible: bridge.canConfirmHolderLatched" in qml
    assert "onClicked: bridge.confirmHolderLatched()" in qml
