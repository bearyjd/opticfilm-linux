"""Lint the Flatpak desktop entry, AppStream metainfo and icon against the manifest."""

import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "packaging" / "flatpak"
APP_ID = "io.github.bearyjd.OpticFilmLinux"
MANIFEST = json.loads((DIR / f"{APP_ID}.json").read_text())
DESKTOP = DIR / f"{APP_ID}.desktop"
METAINFO = DIR / f"{APP_ID}.metainfo.xml"
ICON = DIR / f"{APP_ID}.svg"
INSTALLS = {
    DESKTOP: f"/app/share/applications/{APP_ID}.desktop",
    METAINFO: f"/app/share/metainfo/{APP_ID}.metainfo.xml",
    ICON: f"/app/share/icons/hicolor/scalable/apps/{APP_ID}.svg",
}
GPL_COMPATIBLE = {"GPL-3.0-or-later", "GPL-3.0-only"}


def app_build_commands() -> list[str]:
    module = next(m for m in MANIFEST["modules"] if m["name"] == "opticfilm-linux")
    return module["build-commands"]


def desktop_keys() -> dict[str, str]:
    lines = DESKTOP.read_text().splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line)


def test_files_exist_and_use_the_manifest_app_id():
    assert MANIFEST["app-id"] == APP_ID
    for path in INSTALLS:
        assert path.is_file(), path


@pytest.mark.parametrize("path", list(INSTALLS))
def test_manifest_installs_each_file_to_its_standard_path(path):
    wanted = f"packaging/flatpak/{path.name}"
    commands = [c for c in app_build_commands() if wanted in c]
    assert len(commands) == 1
    assert commands[0].startswith("install -Dm644 ") and commands[0].endswith(INSTALLS[path])


def test_desktop_entry_launches_the_manifest_command_with_the_icon():
    keys = desktop_keys()
    assert keys["Exec"] == MANIFEST["command"]
    assert keys["Icon"] == APP_ID
    assert keys["Type"] == "Application" and keys["Name"]


def test_metainfo_has_the_required_fields_and_the_repo_license():
    root = ET.parse(METAINFO).getroot()
    assert root.get("type") == "desktop-application"
    assert root.findtext("id") == APP_ID
    for tag in ("name", "summary", "description"):
        assert (root.findtext(tag) or "").strip() or root.find(tag) is not None
    assert root.findtext("project_license") in GPL_COMPATIBLE
    assert root.find("content_rating") is not None
    assert root.find("releases/release").get("version")
    assert root.findtext("launchable") == f"{APP_ID}.desktop"


def test_metainfo_license_matches_pyproject_and_the_license_file():
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert 'license = "GPL-3.0-or-later"' in pyproject
    assert "GNU GENERAL PUBLIC LICENSE" in (ROOT / "LICENSE").read_text()
    assert ET.parse(METAINFO).getroot().findtext("project_license") == "GPL-3.0-or-later"


def test_release_version_matches_pyproject():
    pyproject = (ROOT / "pyproject.toml").read_text()
    version = ET.parse(METAINFO).getroot().find("releases/release").get("version")
    assert f'version = "{version}"' in pyproject


def test_icon_is_a_well_formed_svg():
    root = ET.parse(ICON).getroot()
    assert root.tag == "{http://www.w3.org/2000/svg}svg"


def test_talk_name_permission_is_documented_with_its_security_implication():
    assert "--talk-name=org.freedesktop.Flatpak" in MANIFEST["finish-args"]
    notes = [n for n in MANIFEST["x-notes"] if "--talk-name=org.freedesktop.Flatpak" in n]
    assert notes and "Security implication" in notes[0] and "flatpak-spawn --host" in notes[0]


@pytest.mark.skipif(shutil.which("desktop-file-validate") is None, reason="not installed")
def test_desktop_file_validate_passes():
    done = subprocess.run(["desktop-file-validate", str(DESKTOP)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.skipif(shutil.which("appstreamcli") is None, reason="not installed")
def test_appstreamcli_validate_passes_offline():
    done = subprocess.run(
        ["appstreamcli", "validate", "--no-net", str(METAINFO)], capture_output=True, text=True
    )
    assert done.returncode == 0, done.stdout + done.stderr
