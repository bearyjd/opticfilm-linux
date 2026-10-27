"""Lint of the Flatpak manifest draft (packaging/flatpak). Offline: no build, no downloads."""

import copy
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "packaging" / "flatpak" / "io.github.bearyjd.OpticFilm.json"
REQUIRED_KEYS = ("app-id", "runtime", "runtime-version", "sdk", "command", "finish-args", "modules")
SHA256 = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
PLACEHOLDER = re.compile(r"PLACEHOLDER_[A-Z0-9_]+")
SECRET = re.compile(
    r"(sk-[A-Za-z0-9]{10,}|gh[pousr]_[A-Za-z0-9]{10,}|api[_-]?key|secret|password|token="
    r"|BEGIN [A-Z ]*PRIVATE)",
    re.IGNORECASE,
)
VENDOR_HINTS = ("plustek", ".dll", ".exe", ".msi", "scanapi", "vendor")
DEVICE_ARGS = {"--device=all", "--device=usb"}


def sources(manifest: dict):
    for module in manifest.get("modules", []):
        yield from module.get("sources", [])


def lint(manifest: dict, text: str) -> list[str]:
    problems = [f"missing key {k}" for k in REQUIRED_KEYS if not manifest.get(k)]
    args = manifest.get("finish-args") or []
    if not DEVICE_ARGS & set(args):
        problems.append("no USB device permission in finish-args")
    notes = manifest.get("x-notes") or []
    if "--device=all" in args and not any("--device=all" in n for n in notes):
        problems.append("--device=all needs a note explaining why it is broad")
    if any(a.startswith("--share=network") for a in args):
        problems.append("runtime network access in finish-args")
    if any(a in ("--filesystem=host", "--filesystem=home", "--filesystem=/") for a in args):
        problems.append("overbroad filesystem access")
    for module in manifest.get("modules", []):
        build_args = (module.get("build-options") or {}).get("build-args", [])
        if "--share=network" in build_args:
            problems.append(f"{module.get('name')}: network at build time")
    for src in sources(manifest):
        url = src.get("url", "")
        if not url.startswith("https://"):
            problems.append(f"source url not https: {url!r}")
        if any(h in url.lower() for h in VENDOR_HINTS):
            problems.append(f"vendor material in source: {url!r}")
        if src.get("type") == "git" and not (
            COMMIT.fullmatch(src.get("commit", "")) or PLACEHOLDER.fullmatch(src.get("commit", ""))
        ):
            problems.append(f"git source not pinned to a commit: {url!r}")
        if src.get("type") in ("file", "archive") and not (
            SHA256.fullmatch(src.get("sha256", "")) or PLACEHOLDER.fullmatch(src.get("sha256", ""))
        ):
            problems.append(f"file source without sha256: {url!r}")
        if src.get("type") not in ("git", "file", "archive"):
            problems.append(f"unexpected source type {src.get('type')!r} (nothing is vendored)")
    if SECRET.search(text):
        problems.append("possible secret in manifest")
    return problems


def test_manifest_parses_and_lints_clean():
    text = MANIFEST.read_text()
    assert lint(json.loads(text), text) == []


def mutated(change) -> list[str]:
    manifest = copy.deepcopy(json.loads(MANIFEST.read_text()))
    change(manifest)
    return lint(manifest, json.dumps(manifest))


@pytest.mark.parametrize("key", REQUIRED_KEYS)
def test_missing_required_key_is_reported(key):
    assert any(key in p for p in mutated(lambda m: m.pop(key)))


def test_missing_usb_permission_is_reported():
    def drop(m):
        m["finish-args"] = [a for a in m["finish-args"] if not a.startswith("--device")]

    assert any("USB" in p for p in mutated(drop))


def test_runtime_network_is_reported():
    assert any(
        "network" in p for p in mutated(lambda m: m["finish-args"].append("--share=network"))
    )


def test_build_time_network_is_reported():
    def add(m):
        m["modules"][0]["build-options"] = {"build-args": ["--share=network"]}

    assert any("build time" in p for p in mutated(add))


def test_home_access_is_reported():
    assert any(
        "filesystem" in p for p in mutated(lambda m: m["finish-args"].append("--filesystem=home"))
    )


def test_unpinned_git_source_is_reported():
    def unpin(m):
        git = next(s for s in sources(m) if s["type"] == "git")
        git.pop("commit")
        git["branch"] = "main"

    assert any("pinned" in p for p in mutated(unpin))


def test_file_source_without_hash_is_reported():
    def unhash(m):
        next(s for s in sources(m) if s["type"] == "file").pop("sha256")

    assert any("sha256" in p for p in mutated(unhash))


def test_vendor_source_is_reported():
    def vendor(m):
        m["modules"][0]["sources"].append(
            {"type": "file", "url": "https://example.org/Scanapi_x64.dll", "sha256": "0" * 64}
        )

    assert any("vendor" in p for p in mutated(vendor))


def test_vendored_local_source_is_reported():
    def local(m):
        m["modules"][0]["sources"].append({"type": "dir", "path": "vendor-driver"})

    assert any("unexpected source type" in p for p in mutated(local))


def test_non_https_source_is_reported():
    def http(m):
        next(s for s in sources(m) if s["type"] == "git")["url"] = "http://example.org/x.git"

    assert any("https" in p for p in mutated(http))


def test_secret_is_reported():
    assert any("secret" in p for p in mutated(lambda m: m.update({"x-token": "api_key=abc"})))


def test_broad_device_without_note_is_reported():
    assert any("broad" in p for p in mutated(lambda m: m.pop("x-notes")))


def test_driver_is_pinned_as_external_dependency():
    manifest = json.loads(MANIFEST.read_text())
    git_urls = [s["url"] for s in sources(manifest) if s["type"] == "git"]
    assert "https://github.com/cgillinger/opticfilm135i-linux.git" in git_urls
    assert any("EXTERNAL" in note for note in manifest["x-notes"])


def test_python_dependencies_cover_pyproject():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    wanted = {re.split(r"[<>=!~ ]", d, maxsplit=1)[0].lower().replace("-", "_") for d in project}
    urls = " ".join(
        s["url"].lower().replace("-", "_") for s in sources(json.loads(MANIFEST.read_text()))
    )
    assert [name for name in wanted if name not in urls] == []


def test_unpinned_placeholders_are_declared_in_the_notes():
    manifest = json.loads(MANIFEST.read_text())
    assert any("PLACEHOLDER" in n and "NOT BUILDABLE" in n for n in manifest["x-notes"])


@pytest.mark.skipif(shutil.which("flatpak-builder") is None, reason="flatpak-builder not installed")
def test_flatpak_builder_accepts_the_manifest():
    done = subprocess.run(
        ["flatpak-builder", "--show-manifest", str(MANIFEST)], capture_output=True, text=True
    )
    assert done.returncode == 0 and "WARNING" not in done.stderr and "ERROR" not in done.stderr
    shown = json.loads(done.stdout)
    assert shown["id"] == "io.github.bearyjd.OpticFilm" and shown["command"] == "opticfilm"
    assert [m["name"] for m in shown["modules"]] == [
        m["name"] for m in json.loads(MANIFEST.read_text())["modules"]
    ]


@pytest.mark.skipif(shutil.which("flatpak-builder") is None, reason="flatpak-builder not installed")
def test_flatpak_builder_flags_a_malformed_manifest(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"app-id": 1, "modules": "x"}')
    done = subprocess.run(
        ["flatpak-builder", "--show-manifest", str(bad)], capture_output=True, text=True
    )
    assert "WARNING" in done.stderr or done.returncode != 0
