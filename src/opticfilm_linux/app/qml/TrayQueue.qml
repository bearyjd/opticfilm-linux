import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

ListView {
    id: queue
    objectName: "trayQueue"
    signal trayChosen()
    signal recoverRequested(int number)

    clip: true
    model: bridge.trays
    spacing: 1

    QQC2.Label {
        anchors.centerIn: parent
        visible: queue.count === 0
        width: parent.width - Theme.gu * 2
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        opacity: 0.7
        text: qsTr("No trays yet. Load a tray and press Scan when the scanner is ready.")
    }

    delegate: QQC2.ItemDelegate {
        id: row

        required property int number
        required property string stage
        required property string stageText
        required property real progress
        required property string elapsed
        required property string error
        required property int needsLook
        required property string auto
        required property string autoFailed
        required property string autoError
        required property bool canRecoverRaw
        required property string autoFlagged

        width: ListView.view.width
        highlighted: number === bridge.selectedTray
        // Keep error text readable on selected rows in both themes.
        background: Rectangle {
            color: row.highlighted ? Theme.track : row.hovered ? Theme.card : "transparent"
            border.color: row.highlighted || row.visualFocus ? Theme.accent : "transparent"
            border.width: 1
        }
        onClicked: { bridge.selectTray(number, 0); queue.trayChosen(); }
        MouseArea {
            anchors.fill: parent
            z: 1
            onClicked: mouse => { bridge.selectTray(row.number, mouse.modifiers); queue.trayChosen(); }
        }

        contentItem: ColumnLayout {
            z: 2
            spacing: Kirigami.Units.smallSpacing

            RowLayout {
                QQC2.Label {
                    text: qsTr("Tray %1").arg(row.number)
                    font.bold: true
                    Layout.fillWidth: true
                }
                QQC2.Label {
                    text: row.stageText + (row.elapsed !== "" ? " · " + row.elapsed : "")
                    opacity: 0.8
                }
            }
            QQC2.ProgressBar {
                Layout.fillWidth: true
                visible: row.stage === "scanning" || row.stage === "scanned" || row.stage === "enhancing"
                indeterminate: row.progress < 0
                value: Math.max(0, row.progress)
            }
            QQC2.Label {
                objectName: "trayAutoStatus" + row.number
                visible: row.auto !== ""
                text: row.auto + (row.autoError !== "" ? ": " + row.autoError : "")
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
            QQC2.Label {
                objectName: "trayAutoSlides" + row.number
                visible: row.autoFailed !== "" || row.autoFlagged !== ""
                text: (row.autoFailed !== "" ? qsTr("Failed slides: %1. ").arg(row.autoFailed) : "") + (row.autoFlagged !== "" ? qsTr("Flagged slides: %1").arg(row.autoFlagged) : "")
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
            QQC2.Button {
                objectName: "recoverRaw" + row.number
                visible: row.canRecoverRaw && !bridge.readonly
                text: qsTr("Verify remaining scans…")
                onClicked: queue.recoverRequested(row.number)
            }
            QQC2.Label {
                visible: row.stage === "review" && row.needsLook > 0
                text: qsTr("%1 need a look").arg(row.needsLook)
                color: Kirigami.Theme.neutralTextColor
            }
            QQC2.Label {
                objectName: "trayError" + row.number
                visible: row.error !== ""
                text: row.error
                color: Theme.error
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
        }
    }
}
