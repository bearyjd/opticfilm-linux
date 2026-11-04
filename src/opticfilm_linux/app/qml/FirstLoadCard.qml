import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

QQC2.Frame {
    id: card
    objectName: "firstLoadCard"
    property bool compact: false
    signal terminalHelp()
    readonly property bool active: ["jogging", "await_reinsert", "loading"].indexOf(bridge.loadPhase) >= 0
    readonly property bool available: bridge.canFirstLoad || active || bridge.loadPhase === "failed" || bridge.loadPhase === "done"
    visible: available
    Layout.fillWidth: true
    Layout.margins: Kirigami.Units.smallSpacing

    Binding {
        target: cardColumn
        property: "spacing"
        value: Theme.spacingSmall
        when: card.compact
    }

    ConfirmDialog {
        id: cancelDialog
        objectName: "firstLoadCancelDialog"
        title: qsTr("Cancel first load?")
        subtitle: qsTr("Cancelling needs a power cycle afterwards. Switch the scanner off until the lights go out, then switch it on before loading again.")
        onConfirmed: bridge.firstLoadCancel()
    }

    // Regular size keeps the Frame's default padding and the layout's default spacing, exactly as
    // before contentItem replaced anchors.fill (that also sat inside the padded content item);
    // compact tightens both.
    Binding {
        target: card
        property: "padding"
        value: Theme.spacingSmall
        when: card.compact
    }

    contentItem: ColumnLayout {
        id: cardColumn
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
        Flow {
            Layout.fillWidth: true
            spacing: card.compact ? Kirigami.Units.smallSpacing : Kirigami.Units.largeSpacing
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
