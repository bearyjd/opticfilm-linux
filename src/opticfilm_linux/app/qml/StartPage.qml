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
    readonly property string automaticExportProblem: autoExportSwitch.checked ? bridge.automaticExportProblem : ""
    readonly property var automaticExportInputs: [autoExportSwitch.checked, parentFolder.toString(), nameField.text, exportFolder.toString()]
    readonly property string dateText: dateField.dateText
    // Create stays disabled while the date is invalid
    readonly property string dateProblem: dateField.dateProblem
    readonly property bool showDateProblem: dateField.showDateProblem
    readonly property string dateHelp: qsTr("A guess is fine. Saved as approximate.")
    // What the bridge proposes for the text in "What do you know"; asking it changes nothing
    readonly property var guess: bridge.guessBatch(knowField.text)
    // The date Use suggestion would apply: the default, or the chip the owner picked
    property string pendingDate: ""
    // The date of the previous guess: editing the text keeps a picked chip while the parsed date is unchanged
    property string guessDate: ""
    onGuessChanged: {
        if (guess.date !== guessDate) {
            guessDate = guess.date;
            pendingDate = guess.date;
        }
    }
    // The default (when it is not already one of the alternatives) followed by the alternatives
    readonly property var guessChoices: guess.date === "" || guess.alternatives.indexOf(guess.date) >= 0 ? guess.alternatives : [guess.date].concat(guess.alternatives)
    readonly property var monthNames: [qsTr("Jan"), qsTr("Feb"), qsTr("Mar"), qsTr("Apr"), qsTr("May"), qsTr("Jun"), qsTr("Jul"), qsTr("Aug"), qsTr("Sep"), qsTr("Oct"), qsTr("Nov"), qsTr("Dec")]

    function choiceLabel(text) {
        const parts = text.split("-");
        return parts.length > 1 ? monthNames[parseInt(parts[1], 10) - 1] + " " + parts[0] : parts[0];
    }
    function pickGuess(value) {
        pendingDate = value;
    }
    // Fills only the fields the owner left empty; typed values stay as they are
    function applyGuess() {
        if (descriptionField.text === "")
            descriptionField.text = guess.description;
        if (pendingDate !== "" && dateField.dateText === "")
            dateField.setDate(pendingDate);
    }
    onAutomaticExportInputsChanged: bridge.checkAutomaticExport(...automaticExportInputs)
    Component.onCompleted: exportFolder = bridge.lastExportFolder
    background: Rectangle {
        color: Theme.window
    }
    padding: Theme.spacing
    verticalScrollBarPolicy: QQC2.ScrollBar.AlwaysOn

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
        spacing: Theme.spacing
        ErrorBanner {
            Layout.fillWidth: true
        }
        Kirigami.Heading {
            text: qsTr("OpticFilm Linux")
            level: 1
        }
        QQC2.Label {
            font.pixelSize: Theme.fontBody
            text: qsTr("Scan four-slide trays, review the results and export finished slides.")
            color: Theme.muted
        }
        Kirigami.Heading {
            text: qsTr("New batch")
            level: 2
        }
        Card {
            objectName: "newBatchCard"
            Layout.fillWidth: true
            contentItem: Kirigami.FormLayout {
                Layout.fillWidth: true
                RowLayout {
                    Kirigami.FormData.label: qsTr("Folder:")
                    QQC2.Label {
                        color: Theme.text
                        font.pixelSize: Theme.fontBody
                        text: page.parentFolder.toString() === "" ? qsTr("(none chosen)") : decodeURIComponent(page.parentFolder.toString().replace("file://", ""))
                        elide: Text.ElideMiddle
                        Layout.maximumWidth: Theme.gu * 24
                    }
                    PillButton {
                        text: qsTr("Browse…")
                        icon.name: "folder-open"
                        onClicked: folderDialog.open()
                    }
                }
                QQC2.TextArea {
                    id: knowField
                    objectName: "newBatchKnow"
                    Kirigami.FormData.label: qsTr("What do you know about this batch?")
                    Kirigami.FormData.labelAlignment: Qt.AlignTop
                    placeholderText: qsTr("e.g. Maine trip, summer 1974, Kodachrome, Grandma's camera")
                    wrapMode: TextEdit.Wrap
                    Layout.fillWidth: true
                    Layout.maximumWidth: Theme.gu * 28
                    Layout.preferredHeight: Theme.gu * 6
                    // a huge paste would make every keystroke slow
                    readonly property int maximumTextLength: 20000
                    onTextChanged: if (length > maximumTextLength) remove(maximumTextLength, length)
                    Accessible.name: qsTr("What do you know about this batch?")
                    Accessible.description: qsTr("Type what you know; a description and an estimated date are suggested below. Ctrl+Enter uses the suggestion.")
                    // Tab moves on instead of typing a tab character
                    Keys.onTabPressed: nextItemInFocusChain(true).forceActiveFocus(Qt.TabFocusReason)
                    Keys.onBacktabPressed: nextItemInFocusChain(false).forceActiveFocus(Qt.BacktabFocusReason)
                    Keys.onPressed: event => {
                        if ((event.modifiers & Qt.ControlModifier) && (event.key === Qt.Key_Return || event.key === Qt.Key_Enter)) {
                            page.applyGuess();
                            event.accepted = true;
                        }
                    }
                }
                ColumnLayout {
                    objectName: "newBatchGuess"
                    visible: page.guess.description !== ""
                    spacing: Theme.spacingSmall
                    Layout.maximumWidth: Theme.gu * 28
                    QQC2.Label {
                        objectName: "newBatchGuessText"
                        Layout.fillWidth: true
                        font.pixelSize: Theme.fontBody
                        color: Theme.text
                        wrapMode: Text.WordWrap
                        text: page.pendingDate === "" ? qsTr("Suggestion: use what you typed as the description. No date yet.") : qsTr("Suggestion: use what you typed as the description, date %1.").arg(page.choiceLabel(page.pendingDate))
                    }
                    Flow {
                        objectName: "newBatchGuessChips"
                        visible: page.guessChoices.length > 1
                        Layout.fillWidth: true
                        spacing: Theme.spacingSmall
                        Repeater {
                            objectName: "newBatchGuessRepeater"
                            model: page.guessChoices
                            delegate: QQC2.Button {
                                required property string modelData
                                objectName: "newBatchGuessChip"
                                property string value: modelData
                                text: page.choiceLabel(modelData)
                                checkable: true
                                checked: modelData === page.pendingDate
                                highlighted: checked
                                Accessible.name: qsTr("Use the date %1").arg(text)
                                onClicked: page.pickGuess(modelData)
                            }
                        }
                    }
                    Repeater {
                        model: page.guess.notes
                        delegate: QQC2.Label {
                            required property string modelData
                            objectName: "newBatchGuessNote"
                            Layout.fillWidth: true
                            font.pixelSize: Theme.fontBody
                            color: Theme.muted
                            wrapMode: Text.WordWrap
                            text: modelData
                        }
                    }
                    Flow {
                        Layout.fillWidth: true
                        spacing: Theme.spacingSmall
                        PillButton {
                            objectName: "newBatchGuessApply"
                            text: qsTr("Use suggestion")
                            icon.name: "dialog-ok-apply"
                            QQC2.ToolTip.text: qsTr("Fills the empty description and date. Never changes what you typed.")
                            onClicked: page.applyGuess()
                        }
                        PillButton {
                            objectName: "newBatchGuessReplaceDescription"
                            visible: descriptionField.text !== "" && descriptionField.text !== page.guess.description
                            text: qsTr("Replace my description")
                            onClicked: descriptionField.text = page.guess.description
                        }
                        PillButton {
                            objectName: "newBatchGuessReplaceDate"
                            visible: page.pendingDate !== "" && dateField.dateText !== "" && dateField.dateText !== page.pendingDate
                            text: qsTr("Replace my date")
                            onClicked: dateField.setDate(page.pendingDate)
                        }
                    }
                }
                QQC2.TextField {
                    id: nameField
                    objectName: "newBatchName"
                    Kirigami.FormData.label: qsTr("Name:")
                    placeholderText: qsTr("Family trips")
                }
                QQC2.TextField {
                    id: descriptionField
                    objectName: "newBatchDescription"
                    Kirigami.FormData.label: qsTr("Description:")
                    placeholderText: qsTr("e.g. Lake trip, Grandpa's 70th")
                    maximumLength: 500
                    Accessible.name: qsTr("Description")
                }
                EstimatedDateField {
                    id: dateField
                    Kirigami.FormData.label: qsTr("Date (estimate):")
                    namePrefix: "newBatch"
                    help: page.dateHelp
                }
                QQC2.Label {
                    objectName: "newBatchDateError"
                    visible: page.showDateProblem
                    text: page.dateProblem
                    Accessible.role: Accessible.AlertMessage
                    Accessible.name: page.dateProblem
                    color: Kirigami.Theme.negativeTextColor
                    font.pixelSize: Theme.fontBody
                    wrapMode: Text.WordWrap
                    Layout.maximumWidth: Theme.gu * 24
                }
                QQC2.Label {
                    font.pixelSize: Theme.fontBody
                    text: page.dateHelp
                    color: Theme.muted
                    wrapMode: Text.WordWrap
                    Layout.maximumWidth: Theme.gu * 24
                }
                QQC2.TextField {
                    id: collectionField
                    objectName: "newBatchCollection"
                    Kirigami.FormData.label: qsTr("Collection:")
                    placeholderText: qsTr("Optional folder name")
                }
                QQC2.SpinBox {
                    id: boxField
                    objectName: "newBatchBox"
                    Kirigami.FormData.label: qsTr("Box:")
                    from: 1
                    to: 999999
                    value: 1
                    editable: true
                }
                QQC2.TextField {
                    id: sectionField
                    objectName: "newBatchSection"
                    Kirigami.FormData.label: qsTr("Section:")
                    text: "a"
                    validator: RegularExpressionValidator {
                        regularExpression: /[a-f]|0[1-9]|[1-9][0-9]/
                    }
                }
                QQC2.SpinBox {
                    id: sectionSizeField
                    objectName: "newBatchSectionSize"
                    Kirigami.FormData.label: qsTr("Slides per section:")
                    from: 1
                    to: 999
                    value: 140
                    editable: true
                }
                QQC2.TextField {
                    id: albumField
                    Kirigami.FormData.label: qsTr("Album:")
                    placeholderText: qsTr("Optional")
                }
                QQC2.ComboBox {
                    id: dpiBox
                    objectName: "newBatchDpi"
                    Kirigami.FormData.label: qsTr("Resolution (dpi):")
                    model: bridge.dpiChoices.map(dpi => ({
                                value: dpi
                            }))
                    textRole: "value"
                    valueRole: "value"
                    enabled: bridge.demoDpi === 0
                    Component.onCompleted: currentIndex = Math.max(0, bridge.dpiChoices.indexOf(bridge.defaultDpi))
                }
                RowLayout {
                    Kirigami.FormData.label: qsTr("Export folder:")
                    QQC2.Label {
                        color: Theme.text
                        font.pixelSize: Theme.fontBody
                        text: page.exportFolder.toString() === "" ? qsTr("(choose later)") : decodeURIComponent(page.exportFolder.toString().replace("file://", ""))
                        elide: Text.ElideMiddle
                        Layout.maximumWidth: Theme.gu * 24
                    }
                    PillButton {
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
                        {
                            key: "jpeg",
                            label: qsTr("JPEG")
                        },
                        {
                            key: "png8",
                            label: qsTr("8-bit PNG")
                        },
                        {
                            key: "tiff16",
                            label: qsTr("16-bit TIFF (best quality)")
                        }
                    ]
                    textRole: "label"
                    valueRole: "key"
                    Component.onCompleted: currentIndex = Math.max(0, indexOfValue(bridge.lastExportFormat))
                }
                QQC2.ComboBox {
                    id: jpegSizeBox
                    objectName: "newBatchJpegSize"
                    Kirigami.FormData.label: qsTr("JPEG size:")
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
                    enabled: exportFormatBox.currentValue === "jpeg"
                    Component.onCompleted: currentIndex = Math.max(0, indexOfValue(bridge.lastExportJpegSize))
                }
                QQC2.Switch {
                    id: autoExportSwitch
                    objectName: "newBatchAutoExport"
                    Kirigami.FormData.label: qsTr("Automatic export:")
                    text: qsTr("Export JPEGs after each tray")
                    checked: bridge.autoExportDefault
                }
                QQC2.Label {
                    color: Theme.text
                    font.pixelSize: Theme.fontBody
                    visible: autoExportSwitch.checked
                    text: keepRawBox.checked ? qsTr("Raw scans are kept after automatic export.") : qsTr("Raw data is kept until you check and free the first tray. Later trays are freed after verification.")
                    wrapMode: Text.Wrap
                    Layout.maximumWidth: Theme.gu * 28
                }
                QQC2.CheckBox {
                    id: keepRawBox
                    objectName: "newBatchKeepRaw"
                    text: qsTr("Keep raw scans after export")
                    checked: bridge.defaultKeepRaw
                }
                QQC2.Label {
                    color: Theme.text
                    font.pixelSize: Theme.fontBody
                    objectName: "automaticExportProblem"
                    visible: page.automaticExportProblem !== ""
                    text: page.automaticExportProblem
                    wrapMode: Text.Wrap
                    Layout.maximumWidth: Theme.gu * 28
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
                    font.pixelSize: Theme.fontBody
                    text: qsTr("Scan one tray first and check; you can change it later.")
                    color: Theme.muted
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
                    font.pixelSize: Theme.fontBody
                    visible: slotsBox.checked
                    text: qsTr("Applied to that slot in every tray, on top of the slide orientation above. Your own changes to a slide still apply on top.")
                    color: Theme.muted
                    wrapMode: Text.WordWrap
                    Layout.maximumWidth: Theme.gu * 24
                }
                PillButton {
                    objectName: "createBatch"
                    text: qsTr("Create batch")
                    icon.name: "document-new"
                    highlighted: true
                    enabled: page.parentFolder.toString() !== "" && nameField.text.trim() !== "" && page.dateProblem === "" && page.automaticExportProblem === ""
                    onClicked: {
                        // hidden slot rows do not count: the owner said all slots are loaded alike
                        const slots = slotsBox.checked ? [slot1, slot2, slot3, slot4] : [];
                        bridge.newBatch(page.parentFolder.toString(), nameField.text, albumField.text, page.dateText, dpiBox.currentValue, parseInt(rotationBox.currentText), mirrorBox.checked, slots.map(s => s.rotation), slots.map(s => s.mirrored), page.exportFolder.toString(), exportFormatBox.currentValue, jpegSizeBox.currentValue, collectionField.text, boxField.value, sectionField.text, sectionSizeField.value, autoExportSwitch.checked, keepRawBox.checked, descriptionField.text);
                    }
                }
            }
        }

        Kirigami.Heading {
            text: qsTr("Recent batches")
            level: 2
        }
        QQC2.Label {
            font.pixelSize: Theme.fontBody
            visible: bridge.recentBatches.length === 0
            text: qsTr("Batches you open or create will be listed here.")
            color: Theme.muted
        }
        Repeater {
            model: bridge.recentBatches
            delegate: Card {
                objectName: "recentBatchCard"
                required property var modelData
                Layout.fillWidth: true
                contentItem: RowLayout {
                    spacing: Theme.spacingSmall
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0
                        QQC2.Label {
                            color: modelData.exists ? Theme.text : Theme.muted
                            font.pixelSize: Theme.fontBody
                            text: modelData.name
                            font.bold: true
                        }
                        QQC2.Label {
                            font.pixelSize: Theme.fontBody
                            text: modelData.exists ? modelData.path : qsTr("%1 — not found").arg(modelData.path)
                            elide: Text.ElideMiddle
                            Layout.fillWidth: true
                            color: Theme.muted
                        }
                    }
                    PillButton {
                        text: qsTr("Open")
                        enabled: modelData.exists
                        onClicked: bridge.openBatch(modelData.path, false)
                    }
                    PillButton {
                        text: qsTr("Open read-only")
                        enabled: modelData.exists
                        onClicked: bridge.openBatch(modelData.path, true)
                    }
                    QQC2.ToolButton {
                        icon.name: "list-remove"
                        QQC2.ToolTip.text: qsTr("Remove from this list")
                        QQC2.ToolTip.visible: hovered
                        onClicked: bridge.forgetBatch(modelData.path)
                    }
                }
            }
        }
    }
}
