import pytest

from opticfilm_linux.cli import main


def test_version_flag_prints_version(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "ofscan 0.1.0" in capsys.readouterr().out
