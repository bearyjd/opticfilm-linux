import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Kirigami.Dialog {
    id: dialog

    title: qsTr("Free disk space")
    padding: Kirigami.Units.largeSpacing
    standardButtons: Kirigami.Dialog.NoButton
    closePolicy: bridge.freeSpaceRunning ? QQC2.Popup.NoAutoClose : QQC2.Popup.CloseOnEscape
    onClosed: if (bridge.hasFreeSpace && !bridge.freeSpaceRunning) bridge.dismissFreeSpace()

    ColumnLayout {
        spacing: Kirigami.Units.largeSpacing
        implicitWidth: Kirigami.Units.gridUnit * 28

        QQC2.Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: bridge.freeSpaceText
        }
        QQC2.Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            visible: !bridge.freeSpaceDone
            opacity: 0.8
            text: qsTr("Only the raw scans are removed. The exported files and the previews stay. Slides can no longer be adjusted or exported again afterwards.")
        }
        QQC2.ProgressBar {
            Layout.fillWidth: true
            visible: bridge.freeSpaceRunning
            indeterminate: true
        }
        RowLayout {
            Layout.alignment: Qt.AlignRight

            QQC2.Button {
                visible: !bridge.freeSpaceDone && !bridge.freeSpaceRunning
                text: qsTr("Remove raw scans")
                icon.name: "edit-delete"
                highlighted: true
                onClicked: bridge.confirmFreeSpace()
            }
            QQC2.Button {
                text: bridge.freeSpaceDone ? qsTr("Close") : (bridge.freeSpaceRunning ? qsTr("Stop after this tray") : qsTr("Cancel"))
                onClicked: bridge.freeSpaceRunning ? bridge.cancelFreeSpace() : dialog.close()
            }
        }
    }
}
