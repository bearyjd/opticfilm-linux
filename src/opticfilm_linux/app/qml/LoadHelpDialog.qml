import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Kirigami.Dialog {
    id: dialog

    objectName: "loadHelpDialog"
    title: qsTr("How to load the scanner")
    padding: Kirigami.Units.largeSpacing
    standardButtons: Kirigami.Dialog.Close

    ColumnLayout {
        spacing: Kirigami.Units.largeSpacing
        implicitWidth: Kirigami.Units.gridUnit * 32

        QQC2.Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: qsTr("After the scanner is switched on, the first holder is loaded from a terminal. Do these steps in order:")
        }
        QQC2.Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: qsTr("1. Switch the scanner off, then on again. Wait until its light is steady.")
        }
        QQC2.Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: qsTr("2. Slide the holder into the slot loosely. Do not push it in hard yet.")
        }
        QQC2.Label {
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
            QQC2.Button {
                objectName: "copyLoadCommand"
                text: qsTr("Copy command")
                icon.name: "edit-copy"
                onClicked: bridge.copyLoadCommand()
            }
        }
        QQC2.Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: qsTr("4. When the terminal asks, pull the holder fully out, push it back in until it stops, and press Enter.")
        }
        QQC2.Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: qsTr("5. Check that the holder is held: it should not pull out any more.")
        }
        QQC2.Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: qsTr("6. Come back to this window and press Scan.")
        }
        Kirigami.Separator {
            Layout.fillWidth: true
        }
        QQC2.Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: qsTr("Between trays: press Eject holder, swap the holder, push it in, then press Load next holder (experimental), or repeat the steps above.")
        }
        QQC2.Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            opacity: 0.7
            text: qsTr("If the window says the scanner needs a power cycle, switch it off until its lights go out, switch it on, and start again at step 1.")
        }
    }
}
