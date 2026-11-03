import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import QtQuick.Dialogs
import org.kde.kirigami as Kirigami

Kirigami.Dialog {
    id: dialog
    preferredWidth: Math.min(28 * Kirigami.Units.gridUnit, parent ? parent.width * 0.9 : 28 * Kirigami.Units.gridUnit)
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
    objectName: "exportDialog"
    property string folderUrl: ""
    readonly property var formats: [
        {
            key: "tiff16",
            label: qsTr("16-bit TIFF (best quality)")
        },
        {
            key: "png8",
            label: qsTr("8-bit PNG")
        },
        {
            key: "jpeg",
            label: qsTr("JPEG")
        }
    ]
    function resetSettings() {
        folderUrl = bridge.batchExportFolder;
        formatBox.currentIndex = Math.max(0, formatBox.indexOfValue(bridge.batchExportFormat));
        sizeBox.currentIndex = Math.max(0, sizeBox.indexOfValue(bridge.batchJpegSize));
    }
    onOpened: resetSettings()
    Connections {
        target: bridge
        function onBatchReplaced() {
            dialog.resetSettings();
        }
    }
    title: qsTr("Export accepted slides")
    padding: Theme.spacing
    standardButtons: Kirigami.Dialog.NoButton
    closePolicy: bridge.exportRunning ? QQC2.Popup.NoAutoClose : QQC2.Popup.CloseOnEscape
    onClosed: if (bridge.hasExport && !bridge.exportRunning)
        bridge.dismissExport()
    FolderDialog {
        id: folderDialog
        title: qsTr("Export to folder")
        onAccepted: dialog.folderUrl = selectedFolder.toString()
    }
    ConfirmDialog {
        id: reexportConfirm
        objectName: "reexportConfirmDialog"
        title: qsTr("Re-export all accepted slides?")
        subtitle: qsTr("The next export will write new numbered files for all accepted slides. The old files are retained. This cannot be undone.")
        onConfirmed: bridge.reexportAllAccepted()
    }
    ColumnLayout {
        spacing: Theme.spacing
        ColumnLayout {
            visible: !bridge.hasExport
            spacing: Theme.spacing
            QQC2.Label {
                color: Theme.text
                font.pixelSize: Theme.fontBody
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("Writes the accepted slides that have not been exported yet. Slides that need a look and empty windows are never exported. You can export again later to continue.")
            }
            RowLayout {
                QQC2.Label {
                    color: Theme.text
                    font.pixelSize: Theme.fontBody
                    Layout.fillWidth: true
                    elide: Text.ElideMiddle
                    text: dialog.folderUrl === "" ? qsTr("(no folder chosen)") : decodeURIComponent(dialog.folderUrl.replace("file://", ""))
                }
                PillButton {
                    text: qsTr("Browse…")
                    icon.name: "folder-open"
                    onClicked: folderDialog.open()
                }
            }
            QQC2.ComboBox {
                id: formatBox
                objectName: "exportFormat"
                Layout.fillWidth: true
                model: dialog.formats
                textRole: "label"
                valueRole: "key"
            }
            QQC2.ComboBox {
                id: sizeBox
                objectName: "exportSize"
                Layout.fillWidth: true
                model: [
                    {
                        key: "full",
                        label: qsTr("Full size")
                    },
                    {
                        key: "3600",
                        label: qsTr("3600 dpi size")
                    }
                ]
                textRole: "label"
                valueRole: "key"
                enabled: formatBox.currentValue === "jpeg"
            }
        }
        ColumnLayout {
            visible: bridge.hasExport
            spacing: Theme.spacing
            QQC2.ProgressBar {
                Layout.fillWidth: true
                from: 0
                to: Math.max(1, bridge.exportTotal)
                value: bridge.exportDone
            }
            QQC2.Label {
                color: Theme.text
                font.pixelSize: Theme.fontBody
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: bridge.exportFinished ? (bridge.exportCancelled ? qsTr("Cancelled. %1 slides were written; export again to continue.").arg(bridge.exportWritten) : (bridge.exportMessage !== "" ? bridge.exportMessage : qsTr("Done. %1 slides were written.").arg(bridge.exportWritten))) : qsTr("Exporting %1 of %2…").arg(bridge.exportDone).arg(bridge.exportTotal)
            }
        }
    }
    footer: QQC2.Control {
        id: footer
        padding: Theme.spacing
        contentItem: GridLayout {
            columns: footer.availableWidth >= reexportButton.implicitWidth + Math.max(startButton.implicitWidth, exportStateButton.implicitWidth) + columnSpacing ? 2 : 1
            columnSpacing: Theme.spacingSmall
            PillButton {
                id: reexportButton
                Layout.alignment: Qt.AlignRight
                objectName: "reexportAllAccepted"
                text: qsTr("Re-export all accepted")
                visible: !bridge.exportRunning
                onClicked: reexportConfirm.open()
            }
            PillButton {
                id: startButton
                visible: !bridge.hasExport
                objectName: "startExport"
                Layout.alignment: Qt.AlignRight
                text: qsTr("Export")
                icon.name: "document-export"
                highlighted: true
                enabled: dialog.folderUrl !== ""
                onClicked: bridge.startExport(dialog.folderUrl, formatBox.currentValue, sizeBox.currentValue)
            }
            PillButton {
                id: exportStateButton
                visible: bridge.hasExport
                Layout.alignment: Qt.AlignRight
                text: bridge.exportFinished ? qsTr("Close") : qsTr("Cancel")
                flat: true
                icon.name: bridge.exportFinished ? "dialog-close" : "dialog-cancel"
                onClicked: bridge.exportFinished ? dialog.close() : bridge.cancelExport()
            }
        }
    }
}
