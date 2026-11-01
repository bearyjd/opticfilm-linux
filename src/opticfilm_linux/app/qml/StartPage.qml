import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import QtQuick.Dialogs
import org.kde.kirigami as Kirigami

Kirigami.ScrollablePage {
    id: page

    objectName: "startPage"

    property url parentFolder: ""
    property url exportFolder: ""

    Component.onCompleted: exportFolder = bridge.lastExportFolder

    title: qsTr("OpticFilm Linux")

    FolderDialog {
        id: folderDialog

        title: qsTr("Choose where the new batch folder goes")
        onAccepted: page.parentFolder = selectedFolder
    }

    FolderDialog {
        id: exportFolderDialog

        title: qsTr("Export to folder")
        onAccepted: page.exportFolder = selectedFolder
    }

    ColumnLayout {
        spacing: Kirigami.Units.largeSpacing

        ErrorBanner {
            Layout.fillWidth: true
        }

        Kirigami.Heading {
            text: qsTr("OpticFilm Linux")
            level: 1
        }
        QQC2.Label {
            text: qsTr("Scan four-slide trays, review the results and export finished slides.")
            opacity: 0.7
        }

        Kirigami.Heading {
            text: qsTr("New batch")
            level: 2
        }

        Kirigami.FormLayout {
            Layout.fillWidth: true

            RowLayout {
                Kirigami.FormData.label: qsTr("Folder:")

                QQC2.Label {
                    text: page.parentFolder.toString() === "" ? qsTr("(none chosen)") : decodeURIComponent(page.parentFolder.toString().replace("file://", ""))
                    elide: Text.ElideMiddle
                    Layout.maximumWidth: Kirigami.Units.gridUnit * 24
                }
                QQC2.Button {
                    text: qsTr("Browse…")
                    icon.name: "folder-open"
                    onClicked: folderDialog.open()
                }
            }
            QQC2.TextField {
                id: nameField

                objectName: "newBatchName"

                Kirigami.FormData.label: qsTr("Name:")
                placeholderText: qsTr("Family trips")
            }
            QQC2.TextField {
                id: albumField

                Kirigami.FormData.label: qsTr("Album:")
                placeholderText: qsTr("Optional")
            }
            QQC2.TextField {
                id: dateField

                Kirigami.FormData.label: qsTr("Date:")
                placeholderText: qsTr("YYYY-MM-DD (optional)")
            }
            QQC2.ComboBox {
                id: dpiBox
                objectName: "newBatchDpi"

                Kirigami.FormData.label: qsTr("Resolution (dpi):")
                model: bridge.dpiChoices.map(dpi => ({value: dpi}))
                textRole: "value"
                valueRole: "value"
                enabled: bridge.demoDpi === 0
                Component.onCompleted: currentIndex = Math.max(0, bridge.dpiChoices.indexOf(bridge.defaultDpi))
            }
            RowLayout {
                Kirigami.FormData.label: qsTr("Export folder:")

                QQC2.Label {
                    text: page.exportFolder.toString() === "" ? qsTr("(choose later)") : decodeURIComponent(page.exportFolder.toString().replace("file://", ""))
                    elide: Text.ElideMiddle
                    Layout.maximumWidth: Kirigami.Units.gridUnit * 24
                }
                QQC2.Button {
                    text: qsTr("Browse…")
                    icon.name: "folder-open"
                    onClicked: exportFolderDialog.open()
                }
            }
            QQC2.ComboBox {
                id: exportFormatBox
                objectName: "newBatchExportFormat"
                Kirigami.FormData.label: qsTr("Export format:")
                model: [
                    {key: "jpeg", label: qsTr("JPEG")},
                    {key: "png8", label: qsTr("8-bit PNG")},
                    {key: "tiff16", label: qsTr("16-bit TIFF (best quality)")}
                ]
                textRole: "label"
                valueRole: "key"
                Component.onCompleted: currentIndex = Math.max(0, indexOfValue(bridge.lastExportFormat))
            }
            QQC2.ComboBox {
                id: jpegSizeBox
                objectName: "newBatchJpegSize"
                Kirigami.FormData.label: qsTr("JPEG size:")
                model: [{key: "full", label: qsTr("Full size")}, {key: "3600", label: qsTr("3600 dpi size")}]
                textRole: "label"
                valueRole: "key"
                enabled: exportFormatBox.currentValue === "jpeg"
                Component.onCompleted: currentIndex = Math.max(0, indexOfValue(bridge.lastExportJpegSize))
            }
            RowLayout {
                Kirigami.FormData.label: qsTr("Slide orientation:")

                QQC2.ComboBox {
                    id: rotationBox

                    model: bridge.rotationChoices
                    displayText: qsTr("%1° clockwise").arg(currentText)
                }
                QQC2.CheckBox {
                    id: mirrorBox

                    text: qsTr("Mirror")
                }
            }
            QQC2.Label {
                text: qsTr("Scan one tray first and check; you can change it later.")
                opacity: 0.7
            }
            QQC2.CheckBox {
                id: slotsBox

                objectName: "slotOrientationsToggle"
                text: qsTr("Some holder slots are always loaded differently")
            }
            SlotRow {
                id: slot1

                position: 1
                visible: slotsBox.checked
            }
            SlotRow {
                id: slot2

                position: 2
                visible: slotsBox.checked
            }
            SlotRow {
                id: slot3

                position: 3
                visible: slotsBox.checked
            }
            SlotRow {
                id: slot4

                position: 4
                visible: slotsBox.checked
            }
            QQC2.Label {
                visible: slotsBox.checked
                text: qsTr("Applied to that slot in every tray, on top of the slide orientation above. Your own changes to a slide still apply on top.")
                opacity: 0.7
                wrapMode: Text.WordWrap
                Layout.maximumWidth: Kirigami.Units.gridUnit * 24
            }
            QQC2.Button {
                objectName: "createBatch"
                text: qsTr("Create batch")
                icon.name: "document-new"
                highlighted: true
                enabled: page.parentFolder.toString() !== "" && nameField.text.trim() !== ""
                onClicked: {
                    // hidden slot rows do not count: the owner said all slots are loaded alike
                    const slots = slotsBox.checked ? [slot1, slot2, slot3, slot4] : [];
                    bridge.newBatch(page.parentFolder.toString(), nameField.text, albumField.text, dateField.text, dpiBox.currentValue, parseInt(rotationBox.currentText), mirrorBox.checked, slots.map(s => s.rotation), slots.map(s => s.mirrored), page.exportFolder.toString(), exportFormatBox.currentValue, jpegSizeBox.currentValue);
                }
            }
        }

        Kirigami.Heading {
            text: qsTr("Recent batches")
            level: 2
        }

        QQC2.Label {
            visible: bridge.recentBatches.length === 0
            text: qsTr("Batches you open or create will be listed here.")
            opacity: 0.7
        }

        Repeater {
            model: bridge.recentBatches

            delegate: RowLayout {
                required property var modelData

                Layout.fillWidth: true
                opacity: modelData.exists ? 1 : 0.5

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0

                    QQC2.Label {
                        text: modelData.name
                        font.bold: true
                    }
                    QQC2.Label {
                        text: modelData.exists ? modelData.path : qsTr("%1 — not found").arg(modelData.path)
                        elide: Text.ElideMiddle
                        Layout.fillWidth: true
                        opacity: 0.7
                    }
                }
                QQC2.Button {
                    text: qsTr("Open")
                    enabled: modelData.exists
                    onClicked: bridge.openBatch(modelData.path, false)
                }
                QQC2.Button {
                    text: qsTr("Open read-only")
                    enabled: modelData.exists
                    onClicked: bridge.openBatch(modelData.path, true)
                }
                QQC2.Button {
                    icon.name: "list-remove"
                    QQC2.ToolTip.text: qsTr("Remove from this list")
                    QQC2.ToolTip.visible: hovered
                    onClicked: bridge.forgetBatch(modelData.path)
                }
            }
        }
    }
}
