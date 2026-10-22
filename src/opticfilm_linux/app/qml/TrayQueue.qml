import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

ListView {
    id: queue

    clip: true
    model: bridge.trays
    spacing: 1

    QQC2.Label {
        anchors.centerIn: parent
        visible: queue.count === 0
        width: parent.width - Kirigami.Units.gridUnit * 2
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

        width: ListView.view.width
        highlighted: number === bridge.selectedTray
        onClicked: bridge.selectTray(number)

        contentItem: ColumnLayout {
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
                visible: row.stage === "review" && row.needsLook > 0
                text: qsTr("%1 need a look").arg(row.needsLook)
                color: Kirigami.Theme.neutralTextColor
            }
            QQC2.Label {
                visible: row.stage === "failed"
                text: row.error
                color: Kirigami.Theme.negativeTextColor
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
        }
    }
}
