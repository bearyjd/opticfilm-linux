import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import QtQuick.Dialogs
import org.kde.kirigami as Kirigami

Kirigami.Dialog {
    id: dialog
    objectName: "exportDialog"

    property string folderUrl: bridge.lastExportFolder
    readonly property var formats: [
        {key: "tiff16", label: qsTr("16-bit TIFF (best quality)")},
        {key: "png8", label: qsTr("8-bit PNG")},
        {key: "jpeg", label: qsTr("JPEG")}
    ]

    title: qsTr("Export accepted slides")
    padding: Kirigami.Units.largeSpacing
    standardButtons: Kirigami.Dialog.NoButton
    closePolicy: bridge.exportRunning ? QQC2.Popup.NoAutoClose : QQC2.Popup.CloseOnEscape
    onClosed: if (bridge.hasExport && !bridge.exportRunning) bridge.dismissExport()

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
        spacing: Kirigami.Units.largeSpacing
        implicitWidth: Kirigami.Units.gridUnit * 28

        ColumnLayout {
            visible: !bridge.hasExport
            spacing: Kirigami.Units.largeSpacing

            QQC2.Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("Writes the accepted slides that have not been exported yet. Slides that need a look and empty windows are never exported. You can export again later to continue.")
            }
            RowLayout {
                QQC2.Label {
                    Layout.fillWidth: true
                    elide: Text.ElideMiddle
                    text: dialog.folderUrl === "" ? qsTr("(no folder chosen)") : decodeURIComponent(dialog.folderUrl.replace("file://", ""))
                }
                QQC2.Button {
                    text: qsTr("Browse…")
                    icon.name: "folder-open"
                    onClicked: folderDialog.open()
                }
            }
            QQC2.ComboBox {
                id: formatBox

                Layout.fillWidth: true
                model: dialog.formats
                textRole: "label"
                valueRole: "key"
                Component.onCompleted: currentIndex = Math.max(0, indexOfValue(bridge.lastExportFormat))
            }
            QQC2.Button {
                Layout.alignment: Qt.AlignRight
                text: qsTr("Export")
                icon.name: "document-export"
                highlighted: true
                enabled: dialog.folderUrl !== ""
                onClicked: bridge.startExport(dialog.folderUrl, formatBox.currentValue)
            }
        }

        ColumnLayout {
            visible: bridge.hasExport
            spacing: Kirigami.Units.largeSpacing

            QQC2.ProgressBar {
                Layout.fillWidth: true
                from: 0
                to: Math.max(1, bridge.exportTotal)
                value: bridge.exportDone
            }
            QQC2.Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: bridge.exportFinished
                    ? (bridge.exportCancelled
                        ? qsTr("Cancelled. %1 slides were written; export again to continue.").arg(bridge.exportWritten)
                        : (bridge.exportMessage !== ""
                            ? bridge.exportMessage
                            : qsTr("Done. %1 slides were written.").arg(bridge.exportWritten)))
                    : qsTr("Exporting %1 of %2…").arg(bridge.exportDone).arg(bridge.exportTotal)
            }
            QQC2.Button {
                Layout.alignment: Qt.AlignRight
                text: bridge.exportFinished ? qsTr("Close") : qsTr("Cancel")
                icon.name: bridge.exportFinished ? "dialog-close" : "dialog-cancel"
                onClicked: bridge.exportFinished ? dialog.close() : bridge.cancelExport()
            }
        }

        QQC2.Button {
            objectName: "reexportAllAccepted"
            text: qsTr("Re-export all accepted")
            visible: !bridge.exportRunning
            onClicked: reexportConfirm.open()
        }
    }
}
