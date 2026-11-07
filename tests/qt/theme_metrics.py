"""Read production Theme metrics in the caller's QML engine."""

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlComponent, QQmlEngine, qmlEngine

_QML_DIR = Path(__file__).parents[2] / "src/opticfilm_linux/app/qml"
_TOKENS = (
    "gu",
    "cardRadius",
    "spacingSmall",
    "spacing",
    "spacingLarge",
    "fontSmall",
    "fontBody",
    "fontHeading",
    "fontRotation",
    "controlHeight",
    "iconSmall",
    "iconMedium",
    "minCell",
    "maxCell",
    "paneMin",
    "paneMax",
    "inspectorMin",
    "inspectorMax",
)


def theme_metrics(root_or_engine):
    engine = root_or_engine if isinstance(root_or_engine, QQmlEngine) else qmlEngine(root_or_engine)
    assert engine is not None
    values = [f"{name}: Theme.{name}" for name in _TOKENS]
    values += [
        "gridUnit: Theme.gu",
        "kirigamiGridUnit: Kirigami.Units.gridUnit",
        "applicationFontHeight: applicationFontMetrics.height",
        "kirigamiIconSmall: Kirigami.Units.iconSizes.small",
        "kirigamiIconMedium: Kirigami.Units.iconSizes.smallMedium",
        "reviewMinShort: Theme.reviewMin(10 * Theme.gu)",
        "reviewMinTall: Theme.reviewMin(60 * Theme.gu)",
    ]
    component = QQmlComponent(engine)
    component.setData(
        (
            'import QtQuick\nimport org.kde.kirigami as Kirigami\nimport "."\n'
            "QtObject { property FontMetrics applicationFontMetrics: "
            "FontMetrics { font: Qt.application.font } "
            "readonly property var metrics: ({" + ",".join(values) + "}) }"
        ).encode(),
        QUrl.fromLocalFile(str(_QML_DIR / "ThemeMetricsProbe.qml")),
    )
    probe = component.create()
    assert probe is not None, component.errors()
    metrics = probe.property("metrics").toVariant()
    probe.deleteLater()
    return metrics
