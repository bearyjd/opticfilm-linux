import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2

ColumnLayout {
    id: pane
    objectName: "trayPane"
    property bool shortWindow: false
    signal recoverRequested(int number)
    signal reviewFocusRequested()
    signal nextTrayEditRequested()
    readonly property var batchMeta: bridge.selectionSummary.batchMeta || ({})
    spacing: Theme.spacingSmall

    QQC2.Label {
        text: qsTr("TRAYS")
        color: Theme.muted
        font.pixelSize: Theme.fontSmall
        Layout.margins: Theme.spacing
    }
    // The batch default the next scanned tray inherits; editable between holders.
    RowLayout {
        Layout.fillWidth: true
        Layout.leftMargin: Theme.spacing
        Layout.rightMargin: Theme.spacingSmall
        spacing: Theme.spacingSmall
        QQC2.Label {
            objectName: "nextTrayMeta"
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            font.pixelSize: Theme.fontSmall
            color: Theme.text
            text: qsTr("Next tray: %1 · %2").arg(pane.batchMeta.description || qsTr("no description")).arg(pane.batchMeta.date ? "~" + pane.batchMeta.date : qsTr("no date"))
        }
        QQC2.Button {
            objectName: "nextTrayMetaEdit"
            flat: true
            text: qsTr("Edit")
            Accessible.name: qsTr("Edit the description and date of the next tray")
            enabled: !bridge.readonly
            onClicked: pane.nextTrayEditRequested()
        }
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
        // The short-window labels ("Flagged (n)", "Failed (n)") are abbreviations for the eye only;
        // Accessible.name keeps the full "Select flagged/failed (n)" strings.
        PillButton {
            objectName: "selectFlagged"
            flat: true
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
            flat: true
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
