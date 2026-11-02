pragma Singleton
import QtQuick

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
    readonly property color track: dark ? "#39465b" : "#dce4ef"

    readonly property int cardRadius: 14
    readonly property int spacingSmall: 8
    readonly property int spacing: 16
    readonly property int spacingLarge: 24
    readonly property int fontSmall: 12
    readonly property int fontBody: 14
    readonly property int fontHeading: 20
    readonly property int controlHeight: 44
}
