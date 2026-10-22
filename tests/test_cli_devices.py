from opticfilm_linux import cli
from opticfilm_linux.scanner.base import DeviceInfo

SCANNER = DeviceInfo(0x07B3, 0x1436, "Plustek OpticFilm 135i", 3, 54)


def test_devices_reports_none_found(monkeypatch, capsys):
    monkeypatch.setattr(cli, "find_devices", lambda: [])
    assert cli.main(["devices"]) == 0
    assert "no supported scanner found" in capsys.readouterr().out


def test_devices_lists_the_scanner(monkeypatch, capsys):
    monkeypatch.setattr(cli, "find_devices", lambda: [SCANNER])
    assert cli.main(["devices"]) == 0
    assert "07b3:1436 Plustek OpticFilm 135i  bus 3 address 54" in capsys.readouterr().out


def test_scan_without_fake_reports_not_supported(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "find_devices", lambda: [SCANNER])
    assert cli.main(["scan", "--out", str(tmp_path / "out")]) == 2
    assert "not supported" in capsys.readouterr().err.lower()


def test_scan_without_fake_and_without_scanner_says_so(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "find_devices", lambda: [])
    assert cli.main(["scan", "--out", str(tmp_path / "out")]) == 2
    assert "no supported scanner found" in capsys.readouterr().err
