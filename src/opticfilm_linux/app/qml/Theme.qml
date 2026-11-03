// Approved sizing departure: Kirigami gridUnit stays fixed on this stack.
// Follow the application font height above that floor so layout and text grow together.
pragma Singleton
import QtQuick
import org.kde.kirigami as Kirigami

QtObject {
    property bool dark: true

    readonly property color window: dark ? "#171b24" : "#f1f4f9"
    readonly property color card: dark ? "#232a38" : "#ffffff"
    readonly property color text: dark ? "#f1f5fc" : "#172133"
    readonly property color muted: dark ? "#abb8ce" : "#53637b"
    readonly property color accent: dark ? "#83b4ff" : "#255caa"
    readonly property color pressedAccent: dark ? "#a4c8ff" : "#194780"
    readonly property color accentText: dark ? "#102039" : "#ffffff"
    readonly property color line: dark ? "#4b5d78" : "#acbbcf"
    readonly property color warn: dark ? "#f2c14e" : "#8a5a00"
    readonly property color error: dark ? "#ff9a9a" : "#a8201a"
    readonly property color track: dark ? "#39465b" : "#dce4ef"

    readonly property FontMetrics applicationFontMetrics: FontMetrics {
        id: fontMetrics
        font: Qt.application.font
    }
    readonly property int gu: Math.max(Kirigami.Units.gridUnit, fontMetrics.height)
    readonly property int cardRadius: Math.round(gu * 0.8)
    readonly property int spacingSmall: Math.round(gu * 0.45)
    readonly property int spacing: Math.round(gu * 0.9)
    readonly property int spacingLarge: Math.round(gu * 1.33)
    readonly property int fontSmall: Math.round(gu * 0.67)
    readonly property int fontBody: Math.round(gu * 0.78)
    readonly property int fontHeading: Math.round(gu * 1.11)
    readonly property int fontRotation: Math.round(gu * 1.33)
    readonly property int controlHeight: Math.max(44, Math.round(gu * 2.45))
    readonly property int iconSmall: Kirigami.Units.iconSizes.small
    readonly property int iconMedium: Kirigami.Units.iconSizes.smallMedium
    readonly property real minCell: 8 * gu
    readonly property real maxCell: 22 * gu
    readonly property real paneMin: 11 * gu
    readonly property real paneMax: 15 * gu
    readonly property real inspectorMin: 16 * gu
    readonly property real inspectorMax: 22 * gu

    function reviewMin(height: real): real {
        return Math.max(17 * gu, 0.5 * height);
    }
}
