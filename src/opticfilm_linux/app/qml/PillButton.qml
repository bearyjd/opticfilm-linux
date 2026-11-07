import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

QQC2.Button {
    id: control

    implicitWidth: Math.max(5.5 * Theme.gu, implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Theme.controlHeight
    horizontalPadding: Theme.spacingLarge
    font.pixelSize: Theme.fontBody
    font.weight: Font.DemiBold

    Keys.onReturnPressed: if (enabled) click()
    Keys.onEnterPressed: if (enabled) click()

    property bool danger: false
    readonly property color fillColor: danger ? Theme.danger : Theme.accent
    readonly property color pressedColor: danger ? Theme.pressedDanger : Theme.pressedAccent
    readonly property color fillTextColor: danger ? Theme.dangerText : Theme.accentText

    // flat = outlined secondary action (Cancel, Close); otherwise a filled primary pill
    readonly property color labelColor: !enabled ? Theme.muted : flat ? Theme.text : fillTextColor

    contentItem: RowLayout {
        spacing: Theme.spacingSmall
        Kirigami.Icon {
            visible: control.icon.name !== ""
            source: control.icon.name
            color: control.labelColor
            Layout.alignment: Qt.AlignVCenter
            Layout.preferredWidth: Theme.iconSmall
            Layout.preferredHeight: Theme.iconSmall
            Accessible.ignored: true
        }
        Text {
            Layout.fillWidth: true
            text: control.text
            font: control.font
            color: control.labelColor
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    }
    background: Rectangle {
        radius: height / 2
        color: !control.enabled ? Theme.track : control.flat ? (control.down ? Theme.track : "transparent") : control.down ? control.pressedColor : control.fillColor
        border.width: control.flat && control.enabled ? 2 : 0
        border.color: control.fillColor
    }
    Rectangle {
        objectName: "pillFocusRing"
        anchors.fill: parent
        z: 1
        visible: control.visualFocus
        radius: height / 2
        color: "transparent"
        border.width: 2
        border.color: Theme.text
        Rectangle {
            objectName: "pillFocusInnerRing"
            anchors.fill: parent
            anchors.margins: 2
            radius: height / 2
            color: "transparent"
            border.width: 2
            border.color: control.fillTextColor
        }
    }
}
