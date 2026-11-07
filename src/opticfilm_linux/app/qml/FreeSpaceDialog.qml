import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Kirigami.Dialog {
    id: dialog
    preferredWidth: Math.min(28 * Theme.gu, parent ? parent.width * 0.9 : 28 * Theme.gu)
    maximumWidth: preferredWidth
    // Keep centering independent of wrapped body and footer measurements.
    width: preferredWidth
    maximumHeight: parent ? parent.height * 0.9 : Infinity

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
    Binding {
        target: dialog.header
        property: "leftPadding"
        value: dialog.leftPadding
    }
    standardButtons: Kirigami.Dialog.NoButton
    closePolicy: bridge.freeSpaceRunning ? QQC2.Popup.NoAutoClose : QQC2.Popup.CloseOnEscape
    onClosed: if (bridge.hasFreeSpace && !bridge.freeSpaceRunning)
        bridge.dismissFreeSpace()
    ColumnLayout {
        spacing: Theme.spacing
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
    }
    footer: QQC2.Control {
        id: footer
        padding: Theme.spacing
        contentItem: GridLayout {
            columns: footer.availableWidth >= removeButton.implicitWidth + stopButton.implicitWidth + columnSpacing ? 2 : 1
            columnSpacing: Theme.spacingSmall
            PillButton {
                id: stopButton
                Layout.alignment: Qt.AlignLeft
                flat: true
                icon.name: bridge.freeSpaceDone ? "dialog-close" : "dialog-cancel"
                text: bridge.freeSpaceDone ? qsTr("Close") : (bridge.freeSpaceRunning ? qsTr("Stop after this tray") : qsTr("Cancel"))
                onClicked: bridge.freeSpaceRunning ? bridge.cancelFreeSpace() : dialog.close()
            }
            PillButton {
                id: removeButton
                Layout.alignment: Qt.AlignRight
                visible: bridge.freeSpaceReady
                text: qsTr("Remove raw scans")
                icon.name: "edit-delete"
                danger: true
                highlighted: true
                onClicked: bridge.confirmFreeSpace()
            }
        }
    }
}
