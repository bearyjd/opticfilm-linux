"""DPR/theme matrix and system-font probes require zero layout violations."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

WORKER = Path(__file__).with_name("responsive_matrix_worker.py")
ROOT = Path(__file__).resolve().parents[2]
SIZES = (
    (800, 600),
    (1024, 600),
    (1024, 768),
    (1280, 720),
    (1366, 768),
    (1440, 900),
    (1920, 1080),
    (2560, 1440),
    (3840, 2160),
)
FONT_SIZES = ((1024, 600), (1366, 768))


def run_worker(sweep, directory, dpr, font, compact, size):
    output = directory / "result.json"
    env = dict(
        os.environ,
        QT_SCALE_FACTOR=str(dpr),
        QT_QPA_PLATFORM="offscreen",
        QT_QUICK_BACKEND="software",
        PYTHONDONTWRITEBYTECODE="1",
    )
    env["PYTHONPATH"] = os.pathsep.join(str(ROOT / part) for part in ("src", "tests", "tests/qt"))
    for kind in ("STATE", "CACHE", "CONFIG", "DATA"):
        env[f"XDG_{kind}_HOME"] = str(directory / kind.lower())
    command = [sys.executable, str(WORKER), str(sweep), str(directory), str(output), str(font)]
    if compact:
        command.append("--compact")
    if size:
        command.extend(("--size", f"{size[0]}x{size[1]}"))
    completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=300)
    assert completed.returncode == 0, completed.stderr[-6000:]
    return json.loads(output.read_text())


@pytest.fixture(scope="module")
def matrix_run(tmp_path_factory, sweep600_dir):
    cache = {}

    def run(dpr, font, compact=False, size=None):
        key = (str(dpr), str(font), compact, size)
        if key not in cache:
            directory = tmp_path_factory.mktemp("matrix")
            cache[key] = run_worker(sweep600_dir, directory, dpr, font, compact, size)
        return cache[key]

    return run


def assert_no_layout_issues(result):
    failures = [
        f"{case['key']}: {issue} ({count})"
        for case in result["cases"]
        for issue, count in case["issues"].items()
    ]
    assert not failures, "Layout violations:\n" + "\n".join(failures)


def assert_readable(result, dpr):
    assert result["warnings"] == [], result["warnings"]
    for case in result["cases"]:
        assert case["minimum_font"] >= 11, case["key"]
        assert case["dpr"] == pytest.approx(float(dpr)), case["key"]
        for name, size in case["controls"].items():
            assert min(size) >= 24, (case["key"], name, size)


@pytest.mark.qt_matrix
@pytest.mark.parametrize(
    "dpr,font,size",
    [(dpr, 1, size) for dpr in (1, 1.5, 2) for size in SIZES]
    + [(1, 1.5, size) for size in FONT_SIZES],
    ids=lambda value: f"{value[0]}x{value[1]}" if isinstance(value, tuple) else str(value),
)
def test_full_responsive_matrix(matrix_run, dpr, font, size):
    result = matrix_run(dpr, font, size=size)
    assert len(result["cases"]) == 18
    assert all(case["size"] == list(size) for case in result["cases"])
    assert_readable(result, dpr)
    assert_no_layout_issues(result)


@pytest.mark.qt
def test_system_font_probe_remains_readable_without_new_overflow(matrix_run):
    for font in (1, 1.5):
        result = matrix_run(1, font, compact=True)
        assert_readable(result, 1)
        assert_no_layout_issues(result)


@pytest.mark.qt
def test_system_font_scales_text_and_controls_before_engine_creation(matrix_run):
    normal = matrix_run(1, 1, compact=True)
    larger = matrix_run(1, 1.5, compact=True)
    assert larger["font_point_size"] == pytest.approx(normal["font_point_size"] * 1.5)
    for name in ("gridUnit", "fontSmall", "fontBody", "fontHeading", "controlHeight"):
        assert larger["metrics"][name] >= normal["metrics"][name] * 1.3, name
    for base, scaled in zip(normal["cases"], larger["cases"], strict=True):
        assert base["key"] == scaled["key"]
        assert scaled["minimum_font"] >= base["minimum_font"] * 1.3
        for name, (_width, height) in base["controls"].items():
            assert scaled["controls"][name][1] >= height * 1.3, name
