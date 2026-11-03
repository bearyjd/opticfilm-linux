import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Kirigami.Dialog {
    id: dialog
    preferredWidth: Math.min(32 * Kirigami.Units.gridUnit, parent ? parent.width * 0.9 : 32 * Kirigami.Units.gridUnit)
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
    objectName: "loadHelpDialog"
    title: qsTr("How to load the scanner")
    padding: Theme.spacing
    standardButtons: Kirigami.Dialog.Close
    // while the scanner works, the power steps would ruin the scan: they stay hidden throughout
    readonly property bool busy: bridge.scanPending || bridge.holderBusy
    ColumnLayout {
        spacing: Theme.spacing
        Card {
            id: busyWarning
            objectName: "loadBusyWarning"
            Layout.fillWidth: true
            visible: dialog.busy
            borderColor: Theme.warn
            borderWidth: 2
            text: bridge.scanPending ? qsTr("A scan is running. Do NOT switch the scanner off; wait until it finishes.") : qsTr("The holder is moving. Do NOT switch the scanner off; wait until it finishes.")
            Accessible.role: Accessible.AlertMessage
            Accessible.name: text
            contentItem: RowLayout {
                spacing: Theme.spacingSmall
                Kirigami.Icon {
                    source: "dialog-warning"
                    color: Theme.warn
                    Layout.preferredWidth: Theme.spacingLarge
                    Layout.preferredHeight: Theme.spacingLarge
                    Accessible.ignored: true
                }
                Kirigami.SelectableLabel {
                    Layout.fillWidth: true
                    text: busyWarning.text
                    color: Theme.text
                    font.pixelSize: Theme.fontBody
                    wrapMode: Text.Wrap
                }
            }
        }
        ColumnLayout {
            objectName: "loadSteps"
            Layout.fillWidth: true
            visible: !dialog.busy
            spacing: Theme.spacing
            QQC2.Label {
                color: Theme.text
                font.pixelSize: Theme.fontBody
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("You can use Load in the app for the first holder after power-on. To load in a terminal instead, do these steps in order:")
            }
            QQC2.Label {
                color: Theme.text
                font.pixelSize: Theme.fontBody
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("1. Switch the scanner off, then on again. Wait until its light is steady.")
            }
            QQC2.Label {
                color: Theme.text
                font.pixelSize: Theme.fontBody
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("2. Slide the holder into the slot loosely. Do not push it in hard yet.")
            }
            QQC2.Label {
                color: Theme.text
                font.pixelSize: Theme.fontBody
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("3. Open a terminal, paste this command and press Enter:")
            }
            RowLayout {
                Layout.fillWidth: true
                QQC2.TextField {
                    objectName: "loadCommandField"
                    Layout.fillWidth: true
                    readOnly: true
                    selectByMouse: true
                    font.family: "monospace"
                    text: bridge.loadCommand
                }
                PillButton {
                    objectName: "copyLoadCommand"
                    enabled: !dialog.busy
                    text: qsTr("Copy command")
                    icon.name: "edit-copy"
                    onClicked: bridge.copyLoadCommand()
                }
            }
            QQC2.Label {
                font.pixelSize: Theme.fontBody
                objectName: "loadRunnerNote"
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: Theme.muted
                visible: text !== ""
                text: bridge.loadRunnerNote
            }
            QQC2.Label {
                color: Theme.text
                font.pixelSize: Theme.fontBody
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("4. When the terminal asks, pull the holder fully out, push it back in until it stops, and press Enter.")
            }
            QQC2.Label {
                color: Theme.text
                font.pixelSize: Theme.fontBody
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("5. Check that the holder is held: it should not pull out any more.")
            }
            QQC2.Label {
                color: Theme.text
                font.pixelSize: Theme.fontBody
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("6. Come back to this window and press Scan.")
            }
            Kirigami.Separator {
                Layout.fillWidth: true
            }
            QQC2.Label {
                color: Theme.text
                font.pixelSize: Theme.fontBody
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("Between trays: press Eject holder, swap the holder, push it in, then press Load next holder, or repeat the steps above.")
            }
            QQC2.Label {
                font.pixelSize: Theme.fontBody
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: Theme.muted
                text: qsTr("If the window says the scanner needs a power cycle, switch it off until its lights go out, switch it on, and start again at step 1.")
            }
        }
    }

    // Enter activates the only (Close) button: it takes the focus when the dialog opens.
    onAboutToShow: Qt.callLater(() => {
        if (buttonBox.count > 0)
            buttonBox.itemAt(0).forceActiveFocus(Qt.TabFocusReason);
    })

    footer: QQC2.DialogButtonBox {
        id: buttonBox
        standardButtons: dialog.standardButtons
        padding: Theme.spacing
        spacing: Theme.spacingSmall
        background: Item {}
        delegate: PillButton {
            icon.name: "dialog-close"
            highlighted: true
        }
    }
}
