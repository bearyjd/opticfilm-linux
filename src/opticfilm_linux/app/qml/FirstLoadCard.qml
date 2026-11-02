import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

QQC2.Frame {
    id: card
    objectName: "firstLoadCard"
    signal terminalHelp()
    readonly property bool active: ["jogging", "await_reinsert", "loading"].indexOf(bridge.loadPhase) >= 0
    visible: bridge.canFirstLoad || active || bridge.loadPhase === "failed" || bridge.loadPhase === "done"
    Layout.fillWidth: true
    Layout.margins: Kirigami.Units.smallSpacing

    ConfirmDialog {
        id: cancelDialog
        objectName: "firstLoadCancelDialog"
        title: qsTr("Cancel first load?")
        subtitle: qsTr("Cancelling needs a power cycle afterwards. Switch the scanner off until the lights go out, then switch it on before loading again.")
        onConfirmed: bridge.firstLoadCancel()
    }

    ColumnLayout {
        anchors.fill: parent
        QQC2.Label {
            objectName: "firstLoadText"
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: bridge.loadPhase === "await_reinsert" ? qsTr("Pull the holder fully OUT, then push it back in to the stop. Press Done.")
                : bridge.loadPhase === "jogging" ? qsTr("Moving the holder… do not touch it.")
                : bridge.loadPhase === "loading" ? qsTr("Loading… do not touch the holder.")
                : bridge.loadPhase === "done" ? qsTr("Loaded. Press Scan when ready.")
                : bridge.loadPhase === "failed" ? qsTr("First load did not finish. See the error details before trying again.")
                : qsTr("Slide the holder in loosely, then press Load.")
        }
        QQC2.Label {
            objectName: "firstLoadExitCode"
            visible: bridge.loadExitCode !== ""
            text: qsTr("Driver exit code: %1").arg(bridge.loadExitCode)
        }
        RowLayout {
            QQC2.Button {
                objectName: "firstLoadButton"
                text: qsTr("Load")
                visible: !card.active
                enabled: bridge.canFirstLoad
                onClicked: bridge.firstLoad()
            }
            QQC2.Button {
                objectName: "firstLoadDoneButton"
                text: qsTr("Done")
                visible: bridge.loadPhase === "await_reinsert"
                onClicked: bridge.firstLoadDone()
            }
            QQC2.Button {
                objectName: "firstLoadCancelButton"
                text: qsTr("Cancel")
                visible: card.active
                onClicked: cancelDialog.open()
            }
            QQC2.Button {
                objectName: "firstLoadTerminalHelp"
                text: qsTr("I loaded it in a terminal")
                enabled: !card.active
                onClicked: card.terminalHelp()
            }
        }
    }
}
