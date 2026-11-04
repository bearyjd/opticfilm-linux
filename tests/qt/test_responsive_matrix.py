"""Opt-in DPR/theme matrix plus a normal-tier system-font regression probe.

The checked-in baseline records historical layout debt by object name, state,
dimensions, theme, DPR and font. Remaining pane and toolbar work is PR5. Any
additional occurrence fails normally.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from responsive_matrix_baseline import BASELINE

WORKER = Path(__file__).with_name("responsive_matrix_worker.py")
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def matrix_run(tmp_path_factory, sweep600_dir):
    cache = {}

    def run(dpr, font, compact=False):
        key = (str(dpr), str(font), compact)
        if key not in cache:
            directory = tmp_path_factory.mktemp("matrix")
            output = directory / "result.json"
            env = dict(
                os.environ,
                QT_SCALE_FACTOR=str(dpr),
                QT_QPA_PLATFORM="offscreen",
                QT_QUICK_BACKEND="software",
                PYTHONDONTWRITEBYTECODE="1",
            )
            env["PYTHONPATH"] = os.pathsep.join(
                str(ROOT / part) for part in ("src", "tests", "tests/qt")
            )
            for kind in ("STATE", "CACHE", "CONFIG", "DATA"):
                env[f"XDG_{kind}_HOME"] = str(directory / kind.lower())
            command = [
                sys.executable,
                str(WORKER),
                str(sweep600_dir),
                str(directory),
                str(output),
                str(font),
            ]
            if compact:
                command.append("--compact")
            completed = subprocess.run(
                command, env=env, capture_output=True, text=True, timeout=300
            )
            assert completed.returncode == 0, completed.stderr[-6000:]
            cache[key] = json.loads(output.read_text())
        return cache[key]

    return run


def assert_no_new_issues(result, dpr, font):
    expected = BASELINE[f"{float(dpr):g}:{float(font):g}"]
    failures = []
    for case in result["cases"]:
        baseline = expected[case["key"]]
        for issue, count in case["issues"].items():
            if count > baseline.get(issue, 0):
                failures.append(f"{case['key']}: {issue} ({count} > {baseline.get(issue, 0)})")
    assert not failures, "New layout regressions:\n" + "\n".join(failures)


def assert_readable(result, dpr):
    assert result["warnings"] == [], result["warnings"]
    for case in result["cases"]:
        assert case["minimum_font"] >= 11, case["key"]
        assert case["dpr"] == pytest.approx(float(dpr)), case["key"]
        for name, size in case["controls"].items():
            assert min(size) >= 24, (case["key"], name, size)


@pytest.mark.qt_matrix
@pytest.mark.parametrize("dpr,font", ((1, 1), (1.5, 1), (2, 1), (1, 1.5)))
def test_full_responsive_matrix(matrix_run, dpr, font):
    result = matrix_run(dpr, font)
    expected_cases = 36 if font != 1 else 162
    assert len(result["cases"]) == expected_cases
    assert_readable(result, dpr)
    assert_no_new_issues(result, dpr, font)


@pytest.mark.qt
def test_system_font_probe_remains_readable_without_new_overflow(matrix_run):
    for font in (1, 1.5):
        result = matrix_run(1, font, compact=True)
        assert_readable(result, 1)
        assert_no_new_issues(result, 1, font)


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
