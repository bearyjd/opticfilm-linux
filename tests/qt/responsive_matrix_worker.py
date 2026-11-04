"""Fresh-process real-scanner-fixture probes for the optional layout matrix."""

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from layout_fixture import LayoutDialog, create_layout, pump
from layout_probe import assert_reachable, detect, named, tree
from PySide6.QtCore import QUrl
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlComponent
from PySide6.QtWidgets import QApplication

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
CONTROLS = ("primaryActionButton", "ejectButton", "loadNextButton", "acceptSelection")


def metrics(fixture):
    from opticfilm_linux.app.qt import app as app_module

    qml = Path(app_module.__file__).resolve().parents[1] / "qml" / "Metrics.qml"
    component = QQmlComponent(fixture.engine)
    component.setData(
        b"""import QtQml
import org.kde.kirigami as Kirigami
import "."
QtObject {
    property string measured: JSON.stringify({gridUnit: Theme.gu,
        fontSmall: Theme.fontSmall, fontBody: Theme.fontBody,
        fontHeading: Theme.fontHeading, controlHeight: Theme.controlHeight})
}""",
        QUrl.fromLocalFile(str(qml)),
    )
    obj = component.create()
    assert obj is not None, component.errors()
    result = json.loads(obj.property("measured"))
    obj.deleteLater()
    return result


def signature(issue):
    def stable(value):
        return re.sub(r"_QML(?:TYPE)?_\d+", "", value)

    return "|".join((issue.kind, stable(issue.item), stable(issue.ancestor)))


def record(fixture, state, size, theme):
    fixture.apply(state if state == "worst-stack" else "contact-sheet", size, theme)
    dialog = None
    if state not in ("contact-sheet", "worst-stack"):
        dialog = fixture.open_dialog(state)
        fixture.bridge.setTheme(theme)
        pump(50)
    nodes = tree(fixture.engine, fixture.root, dialog)
    issues = detect(fixture.engine, fixture.root, dialog, nodes)
    controls = {} if dialog else measure_controls(fixture, nodes)
    fonts = [n.prop("fontPx") for n in nodes if n.textitem and n.text]
    return {
        "key": f"{size[0]}x{size[1]}:{theme}:{state}",
        "issues": dict(Counter(signature(issue) for issue in issues)),
        "controls": controls,
        "minimum_font": min(fonts, default=100),
        "dpr": fixture.root.devicePixelRatio(),
        "size": [fixture.root.width(), fixture.root.height()],
    }


def measure_controls(fixture, nodes):
    """Measure controls directly or after opening the reachable Inspector drawer."""
    direct = tuple(name for name in CONTROLS if any(n.name == name for n in nodes))
    assert_reachable(nodes, fixture.root, direct)
    controls = {
        name: [named(nodes, name).rect.width, named(nodes, name).rect.height] for name in direct
    }
    missing = set(CONTROLS) - set(direct)
    if missing:
        assert missing == {"acceptSelection"}, missing
        assert_reachable(nodes, fixture.root, ("inspectorDrawerToggle",))
        popup = LayoutDialog(fixture.engine, fixture.root, "inspectorDrawer")
        popup.invoke("open")
        pump(350)
        try:
            drawer_nodes = tree(fixture.engine, fixture.root, popup)
            assert_reachable(drawer_nodes, fixture.root, missing)
            issues = detect(fixture.engine, fixture.root, popup, drawer_nodes)
            assert not issues, issues
            item = named(drawer_nodes, "acceptSelection")
            controls["acceptSelection"] = [item.rect.width, item.rect.height]
        finally:
            popup.invoke("close")
            pump(350)
    assert set(controls) == set(CONTROLS)
    return controls


def run(sweep, directory, font_scale, compact=False):
    app = QApplication(["responsive-matrix"])
    default_font = QFont(app.font())
    font = QFont(default_font)
    font.setPointSizeF(default_font.pointSizeF() * font_scale)
    app.setFont(font)  # Must precede construction of the QML engine and Kirigami Units.
    with create_layout(app, Path(sweep), Path(directory)) as fixture:
        result = {
            "metrics": metrics(fixture),
            "font_scale": font_scale,
            "font_point_size": app.font().pointSizeF(),
            "cases": [],
        }
        sizes = FONT_SIZES if font_scale != 1 or compact else SIZES
        states = (
            ("contact-sheet",)
            if compact
            else ("contact-sheet", "worst-stack", *dict(fixture.dialogs))
        )
        themes = ("dark",) if compact else ("dark", "light")
        for size in sizes:
            for theme in themes:
                for state in states:
                    result["cases"].append(record(fixture, state, size, theme))
        result["warnings"] = fixture.warnings
        return result


def main():
    sweep, directory, output, scale = sys.argv[1:5]
    result = run(sweep, directory, float(scale), "--compact" in sys.argv)
    Path(output).write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
