"""Tests for scripts/tools/tray_budget.py (where a tray's wall time went); synthetic sidecars."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "scripts" / "tools"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


tb = _load("tray_budget")

T0 = dt.datetime(2026, 11, 19, 21, 44, 40, tzinfo=dt.UTC)
BETWEEN = 9.0  # seconds from one opening's end to the next opening's start


def _diag(frame: int, start: float, scan: float = 44.0) -> dict:
    """A sidecar whose scan began ``start`` seconds after T0."""
    phases = {
        "scan": scan,
        "position": 1.5 + 3.5 * (frame - 1),
        "park": 3.5 + 3.5 * (frame - 1),
        "cal_dark_a": 0.1,
        "cal_shading_measure": 2.5,
        "cal_shading_verify": 2.4,
    }
    total = sum(phases.values()) + 0.5
    finished = T0 + dt.timedelta(seconds=start + total)
    return {
        "dpi": 3600,
        "frame": frame,
        "phase_seconds": phases,
        "total_seconds": total,
        "finished_utc": finished.isoformat(),
    }


def _tray(frames: tuple[int, ...] = (1, 2, 3, 4)) -> list[dict]:
    diags, start = [], 0.0
    for frame in frames:
        diag = _diag(frame, start)
        diags.append(diag)
        start += diag["total_seconds"] + BETWEEN
    return diags


def test_budget_separates_the_phases_and_the_time_between_openings():
    budget = tb.tray_budget(_tray())
    assert budget.openings == (1, 2, 3, 4)
    assert budget.scan == pytest.approx(4 * 44.0)
    assert budget.calibration == pytest.approx(4 * 5.0)
    assert budget.position == pytest.approx(1.5 * 4 + 3.5 * 6)
    assert budget.park == pytest.approx(3.5 * 4 + 3.5 * 6)
    assert budget.other == pytest.approx(4 * 0.5)
    assert budget.between == pytest.approx(3 * BETWEEN)
    parts = (
        budget.scan
        + budget.calibration
        + budget.position
        + budget.park
        + budget.other
        + budget.between
    )
    assert budget.wall == pytest.approx(parts)


def test_wall_runs_from_the_first_start_to_the_last_finish():
    diags = _tray((1, 2))
    budget = tb.tray_budget(diags)
    assert budget.wall == pytest.approx(
        diags[0]["total_seconds"] + BETWEEN + diags[1]["total_seconds"]
    )


def test_sidecars_are_ordered_by_finish_time_not_by_argument_order():
    diags = _tray()
    assert tb.tray_budget(list(reversed(diags))) == tb.tray_budget(diags)


def test_opening_cost_is_its_own_time_plus_the_gap_before_it():
    diags = _tray()
    costs = tb.tray_budget(diags).opening_costs
    assert [c.opening for c in costs] == [1, 2, 3, 4]
    assert costs[0].between_before == 0.0
    assert costs[3].between_before == pytest.approx(BETWEEN)
    assert costs[3].seconds == pytest.approx(diags[3]["total_seconds"] + BETWEEN)
    assert costs[3].seconds > costs[1].seconds  # the far opening travels the longest


def test_a_single_opening_has_no_between_time():
    budget = tb.tray_budget(_tray((2,)))
    assert budget.between == 0.0
    assert budget.wall == pytest.approx(_diag(2, 0.0)["total_seconds"])


@pytest.mark.parametrize("missing", ["finished_utc", "total_seconds", "phase_seconds"])
def test_a_sidecar_without_timing_is_refused(missing):
    diags = _tray((1, 2))
    del diags[1][missing]
    with pytest.raises(ValueError, match=missing):
        tb.tray_budget(diags)


def test_no_sidecars_is_refused():
    with pytest.raises(ValueError, match="no sidecars"):
        tb.tray_budget([])


def test_overlapping_scans_are_refused_as_different_runs():
    first, second = _diag(1, 0.0), _diag(2, 10.0)  # the second starts before the first ends
    with pytest.raises(ValueError, match="overlap"):
        tb.tray_budget([first, second])


def test_shares_add_up_to_one():
    shares = tb.shares(tb.tray_budget(_tray()))
    assert set(shares) == {"scan", "calibration", "position", "park", "other", "between"}
    assert sum(shares.values()) == pytest.approx(1.0)


def test_cli_prints_the_budget(tmp_path, capsys):
    paths = []
    for diag in _tray():
        path = tmp_path / f"tray-f{diag['frame']}.diag.json"
        path.write_text(json.dumps(diag))
        paths.append(str(path))
    assert tb.main(paths) == 0
    out = capsys.readouterr().out
    assert "between" in out and "wall" in out
    assert "opening 4" in out


def test_cli_reports_a_bad_sidecar(tmp_path, capsys):
    path = tmp_path / "broken.diag.json"
    path.write_text("{")
    assert tb.main([str(path)]) == 2
    assert "error" in capsys.readouterr().err


@pytest.mark.parametrize("bad", [None, [], [1, 2], "text", 3])
def test_a_sidecar_that_is_not_an_object_is_refused(bad):
    with pytest.raises(ValueError, match="object"):
        tb.tray_budget([bad])


@pytest.mark.parametrize("total", [float("nan"), float("inf"), -1.0, "nan"])
def test_a_non_finite_or_negative_total_is_refused(total):
    diag = _diag(1, 0.0)
    diag["total_seconds"] = total
    with pytest.raises(ValueError, match="total_seconds"):
        tb.tray_budget([diag])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.5])
def test_a_non_finite_or_negative_phase_is_refused(value):
    diag = _diag(1, 0.0)
    diag["phase_seconds"]["scan"] = value
    with pytest.raises(ValueError, match="phase"):
        tb.tray_budget([diag])


@pytest.mark.parametrize("phases", [{}, None, [], "x"])
def test_an_empty_or_malformed_phase_seconds_is_refused(phases):
    diag = _diag(1, 0.0)
    diag["phase_seconds"] = phases
    with pytest.raises(ValueError, match="phase_seconds"):
        tb.tray_budget([diag])


@pytest.mark.parametrize("frame", ["missing", None, "1", 1.5, True])
def test_a_missing_or_non_int_frame_is_refused(frame):
    diag = _diag(1, 0.0)
    if frame == "missing":
        del diag["frame"]
    else:
        diag["frame"] = frame
    with pytest.raises(ValueError, match="frame"):
        tb.tray_budget([diag])


def test_a_duplicate_frame_number_is_refused():
    diags = _tray((1, 2))
    diags[1]["frame"] = 1
    with pytest.raises(ValueError, match="frame 1"):
        tb.tray_budget(diags)


def test_overlapping_phases_warn_and_clamp_other_at_zero(capsys):
    diag = _diag(1, 0.0)
    diag["total_seconds"] -= 5.0  # phases now sum to more than the total
    budget = tb.tray_budget([diag])
    assert budget.other == 0.0
    assert "overlap" in capsys.readouterr().err
