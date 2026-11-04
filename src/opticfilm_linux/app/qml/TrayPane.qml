import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2

ColumnLayout {
    id: pane
    objectName: "trayPane"
    property bool shortWindow: false
    signal recoverRequested(int number)
    signal reviewFocusRequested()
    spacing: Theme.spacingSmall

    QQC2.Label {
        text: qsTr("TRAYS")
        color: Theme.muted
        font.pixelSize: Theme.fontSmall
        Layout.margins: Theme.spacing
    }
    TrayQueue {
        Layout.fillWidth: true
        Layout.fillHeight: true
        onRecoverRequested: number => pane.recoverRequested(number)
        onTrayChosen: pane.reviewFocusRequested()
    }
    GridLayout {
        Layout.fillWidth: true
        Layout.margins: Theme.spacingSmall
        columns: pane.shortWindow ? 2 : 1
        columnSpacing: Theme.spacingSmall
        rowSpacing: Theme.spacingSmall
        PillButton {
            objectName: "selectFlagged"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            horizontalPadding: pane.shortWindow ? Theme.spacingSmall / 2 : Theme.spacingLarge
            font.pixelSize: pane.shortWindow ? Theme.fontSmall : Theme.fontBody
            contentItem: QQC2.Label {
                text: parent.text
                font: parent.font
                color: parent.labelColor
                wrapMode: Text.Wrap
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
            text: pane.shortWindow ? qsTr("Flagged (%1)").arg(bridge.flaggedCount) : qsTr("Select flagged (%1)").arg(bridge.flaggedCount)
            Accessible.name: qsTr("Select flagged (%1)").arg(bridge.flaggedCount)
            onClicked: { bridge.selectFlagged(); pane.reviewFocusRequested(); }
        }
        PillButton {
            objectName: "selectFailed"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            horizontalPadding: pane.shortWindow ? Theme.spacingSmall / 2 : Theme.spacingLarge
            font.pixelSize: pane.shortWindow ? Theme.fontSmall : Theme.fontBody
            contentItem: QQC2.Label {
                text: parent.text
                font: parent.font
                color: parent.labelColor
                wrapMode: Text.Wrap
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
            text: pane.shortWindow ? qsTr("Failed (%1)").arg(bridge.failedCount) : qsTr("Select failed (%1)").arg(bridge.failedCount)
            Accessible.name: qsTr("Select failed (%1)").arg(bridge.failedCount)
            onClicked: { bridge.selectFailed(); pane.reviewFocusRequested(); }
        }
    }
}
