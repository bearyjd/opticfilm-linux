import QtQuick
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import "../../src/opticfilm_linux/app/qml"

QQC2.ApplicationWindow {
    id: root
    width: 640
    height: 360
    visible: true
    color: Theme.window
    property int navigationIndex: 0
    property int activationCount: 0
    readonly property color pressedAccent: Theme.pressedAccent
    readonly property color accentText: Theme.accentText
    readonly property color themeText: Theme.text
    readonly property color themeAccent: Theme.accent
    readonly property color themeCard: Theme.card
    readonly property color themeTrack: Theme.track
    readonly property color themeWindow: Theme.window
    readonly property color disabledPaletteText: palette.disabled.text
    readonly property color inactivePaletteText: palette.inactive.text

    Binding { target: Theme; property: "dark"; value: bridge.theme === "dark" }
    WindowPalette { window: root; colors: bridge.paletteColors }

    Card {
        objectName: "sampleCard"
        x: 20; y: 20; width: 600; height: 320
        contentItem: Item {
            Kirigami.Heading {
                objectName: "sampleHeading"
                x: 20; y: 10
                text: "Theme components"
            }
            PillButton {
                objectName: "samplePill"
                x: 20; y: 60; width: 160; height: 44
                text: "Preview"
            }
            SegmentedControl {
                objectName: "sampleSegments"
                x: 220; y: 60; width: 240; height: 44
                model: ["Dark", "Light"]
                currentIndex: bridge.theme === "dark" ? 0 : 1
                onActivated: function(index) { bridge.setTheme(index === 0 ? "dark" : "light"); }
            }
            QQC2.Slider {
                objectName: "sampleSlider"
                x: 20; y: 160; width: 240; height: 44
                value: 0.6
            }
            QQC2.CheckBox {
                objectName: "sampleCheck"
                x: 300; y: 160; width: 220; height: 44
                text: "Keep accepted"
                checked: true
            }
            QQC2.Button {
                objectName: "disabledButton"
                x: 20; y: 220; width: 150; height: 36
                text: "Disabled button"; enabled: false
            }
            QQC2.Label {
                objectName: "disabledLabel"
                x: 180; y: 220
                text: "Disabled label"; enabled: false
            }
            QQC2.MenuItem {
                objectName: "disabledMenuItem"
                x: 340; y: 220; width: 180; height: 36
                text: "Disabled menu"; enabled: false
            }
            SegmentedControl {
                objectName: "navigationSegments"
                x: 20; y: 112; width: 360; height: 36
                model: ["First", "Middle", "Last"]
                currentIndex: root.navigationIndex
                onActivated: function(index) {
                    root.navigationIndex = index
                    root.activationCount += 1
                }
            }
        }
    }
}
