import QtQuick
import QtQuick.Controls as QQC2

QQC2.Button {
    id: control

    implicitWidth: Math.max(100, implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Theme.controlHeight
    horizontalPadding: Theme.spacingLarge
    font.pixelSize: Theme.fontBody
    font.weight: Font.DemiBold

    contentItem: Text {
        text: control.text
        font: control.font
        color: control.enabled ? Theme.accentText : Theme.muted
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: height / 2
        color: !control.enabled ? Theme.track : control.down ? Theme.pressedAccent : Theme.accent
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
            border.color: Theme.accentText
        }
    }
}
