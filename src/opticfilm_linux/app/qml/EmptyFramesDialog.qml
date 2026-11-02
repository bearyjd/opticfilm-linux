import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Kirigami.Dialog {
    id: dialog
    property int tray: 0
    property int sweepVersion: 0
    property var previews: []
    property int loaded: 0
    objectName: "emptyFramesDialog"
    title: qsTr("Review empty frames in tray %1").arg(tray)
    standardButtons: Kirigami.Dialog.NoButton
    onPreviewsChanged: loaded = 0

    ColumnLayout {
        implicitWidth: Kirigami.Units.gridUnit * 34
        QQC2.Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: qsTr("Check every frame below. Confirm only if all are empty; their raw scans may then be removed.")
        }
        GridLayout {
            Layout.fillWidth: true
            columns: 2
            Repeater {
            model: dialog.previews
            delegate: ColumnLayout {
                required property var modelData
                Layout.fillWidth: true
                QQC2.Label { text: qsTr("Frame %1").arg(modelData.position) }
                Image {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Kirigami.Units.gridUnit * 9
                    fillMode: Image.PreserveAspectFit
                    source: modelData.url
                    property bool counted: false
                    onStatusChanged: {
                        if (status === Image.Ready && !counted && modelData.fingerprint !== "") {
                            counted = true;
                            dialog.loaded += 1;
                        }
                    }
                }
            }
        }
        }
        QQC2.Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            visible: dialog.previews.some(item => item.fingerprint === "" || item.url === "")
            text: qsTr("Some empty frames have no current scan evidence or preview. Rescan this tray to review and confirm them.")
        }
        RowLayout {
            QQC2.Button {
                objectName: "confirmReviewedEmptyFrames"
                text: qsTr("Confirm all these frames are empty")
                enabled: dialog.previews.length > 0 && dialog.loaded === dialog.previews.length
                onClicked: {
                    bridge.confirmEmptyFrames(dialog.tray, dialog.sweepVersion, dialog.previews);
                    dialog.close();
                }
            }
            QQC2.Button { text: qsTr("Cancel"); onClicked: dialog.close() }
        }
    }
}
