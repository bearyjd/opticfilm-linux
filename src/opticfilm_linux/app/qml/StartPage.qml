import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import QtQuick.Dialogs
import org.kde.kirigami as Kirigami

Kirigami.ScrollablePage {
    id: page

    objectName: "startPage"

    property url parentFolder: ""

    title: qsTr("OpticFilm Linux")

    FolderDialog {
        id: folderDialog

        title: qsTr("Choose where the new batch folder goes")
        onAccepted: page.parentFolder = selectedFolder
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

                Kirigami.FormData.label: qsTr("Resolution (dpi):")
                model: bridge.dpiChoices
                enabled: bridge.demoDpi === 0
                Component.onCompleted: currentIndex = Math.max(0, bridge.dpiChoices.indexOf(bridge.defaultDpi))
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
            Repeater {
                id: slotRepeater

                model: slotsBox.checked ? bridge.slotCount : 0
                delegate: RowLayout {
                    id: slotRow

                    required property int index
                    readonly property int rotation: parseInt(slotRotation.currentText)
                    readonly property bool mirrored: slotMirror.checked

                    objectName: "slotOrientation" + (index + 1)
                    Kirigami.FormData.label: qsTr("Holder slot %1:").arg(index + 1)

                    QQC2.ComboBox {
                        id: slotRotation

                        objectName: "slotRotation" + (slotRow.index + 1)
                        model: bridge.rotationChoices
                        displayText: qsTr("%1° more").arg(currentText)
                    }
                    QQC2.CheckBox {
                        id: slotMirror

                        objectName: "slotMirror" + (slotRow.index + 1)
                        text: qsTr("Mirror")
                    }
                }
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
                    let rotations = [];
                    let mirrors = [];
                    for (let i = 0; i < slotRepeater.count; ++i) {
                        rotations.push(slotRepeater.itemAt(i).rotation);
                        mirrors.push(slotRepeater.itemAt(i).mirrored);
                    }
                    bridge.newBatch(page.parentFolder.toString(), nameField.text, albumField.text, dateField.text, parseInt(dpiBox.currentText), parseInt(rotationBox.currentText), mirrorBox.checked, rotations, mirrors);
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
