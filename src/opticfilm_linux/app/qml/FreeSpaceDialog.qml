import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Kirigami.Dialog {
    id: dialog

    Kirigami.Theme.inherit: false
    Kirigami.Theme.backgroundColor: Theme.card
    Kirigami.Theme.alternateBackgroundColor: Theme.track
    Kirigami.Theme.textColor: Theme.text
    Kirigami.Theme.disabledTextColor: Theme.muted
    Kirigami.Theme.highlightColor: Theme.accent
    Kirigami.Theme.highlightedTextColor: Theme.accentText
    Kirigami.Theme.linkColor: Theme.accent

    background: Rectangle {
        objectName: "dialogSurface"
        radius: Theme.cardRadius
        color: Theme.card
        border.color: Theme.line
    }
    title: qsTr("Free disk space")
    padding: Theme.spacing
    standardButtons: Kirigami.Dialog.NoButton
    closePolicy: bridge.freeSpaceRunning ? QQC2.Popup.NoAutoClose : QQC2.Popup.CloseOnEscape
    onClosed: if (bridge.hasFreeSpace && !bridge.freeSpaceRunning)
        bridge.dismissFreeSpace()
    ColumnLayout {
        spacing: Theme.spacing
        implicitWidth: Kirigami.Units.gridUnit * 28
        QQC2.Label {
            color: Theme.text
            font.pixelSize: Theme.fontBody
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: bridge.freeSpaceText
        }
        QQC2.Label {
            font.pixelSize: Theme.fontBody
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            visible: !bridge.freeSpaceDone
            color: Theme.muted
            text: qsTr("Only the raw scans are removed. The exported files and the previews stay. Slides can no longer be adjusted or exported again afterwards.")
        }
        QQC2.ProgressBar {
            Layout.fillWidth: true
            visible: bridge.freeSpaceRunning
            indeterminate: true
        }
        RowLayout {
            Layout.alignment: Qt.AlignRight
            PillButton {
                visible: bridge.freeSpaceReady
                text: qsTr("Remove raw scans")
                icon.name: "edit-delete"
                highlighted: true
                onClicked: bridge.confirmFreeSpace()
            }
            PillButton {
                flat: true
                icon.name: bridge.freeSpaceDone ? "dialog-close" : "dialog-cancel"
                text: bridge.freeSpaceDone ? qsTr("Close") : (bridge.freeSpaceRunning ? qsTr("Stop after this tray") : qsTr("Cancel"))
                onClicked: bridge.freeSpaceRunning ? bridge.cancelFreeSpace() : dialog.close()
            }
        }
    }
}
