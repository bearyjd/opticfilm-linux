"""Production sizing tokens follow the running Kirigami font metrics."""

import math
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = pytest.mark.qt
pytest.importorskip("PySide6")

from PySide6.QtQml import QQmlEngine
from PySide6.QtWidgets import QApplication
from theme_metrics import theme_metrics

from opticfilm_linux.app.qt.app import configure_style


@pytest.fixture
def metrics():
    app = QApplication.instance() or QApplication(["theme-units"])
    configure_style()
    engine = QQmlEngine()
    yield theme_metrics(engine)
    engine.deleteLater()
    assert app is not None


@pytest.mark.parametrize(
    ("token", "factor"),
    [
        ("cardRadius", 0.8),
        ("spacingSmall", 0.45),
        ("spacing", 0.9),
        ("spacingLarge", 1.33),
        ("fontSmall", 0.67),
        ("fontBody", 0.78),
        ("fontHeading", 1.11),
        ("fontRotation", 1.33),
    ],
)
def test_rounded_theme_tokens_match_grid_units(metrics, token, factor):
    effective = max(metrics["kirigamiGridUnit"], metrics["applicationFontHeight"])
    assert metrics["gu"] == math.floor(effective + 0.5)
    assert metrics[token] == math.floor(metrics["gu"] * factor + 0.5)


@pytest.mark.parametrize(
    ("token", "factor"),
    [
        ("minCell", 8),
        ("maxCell", 22),
        ("paneMin", 11),
        ("paneMax", 15),
        ("inspectorMin", 16),
        ("inspectorMax", 22),
    ],
)
def test_layout_bounds_match_grid_units(metrics, token, factor):
    assert metrics[token] == metrics["gu"] * factor


def test_control_touch_target_and_kirigami_icon_tokens(metrics):
    assert metrics["controlHeight"] == max(44, math.floor(metrics["gu"] * 2.45 + 0.5))
    assert metrics["iconSmall"] == metrics["kirigamiIconSmall"]
    assert metrics["iconMedium"] == metrics["kirigamiIconMedium"]


def test_review_minimum_reserves_font_relative_and_proportional_space(metrics):
    assert metrics["reviewMinShort"] == 17 * metrics["gu"]
    assert metrics["reviewMinTall"] == 30 * metrics["gu"]


def test_default_font_preserves_grid_unit(metrics):
    assert metrics["gu"] == 18


def test_smaller_application_font_keeps_kirigami_floor():
    app = QApplication.instance() or QApplication(["theme-units-small-font"])
    original_font = app.font()
    smaller_font = app.font()
    smaller_font.setPointSizeF(original_font.pointSizeF() * 0.5)
    engine = None
    try:
        app.setFont(smaller_font)
        engine = QQmlEngine()
        measured = theme_metrics(engine)
        assert measured["applicationFontHeight"] < measured["kirigamiGridUnit"]
        assert measured["gu"] == measured["kirigamiGridUnit"]
    finally:
        if engine is not None:
            engine.deleteLater()
        app.setFont(original_font)
