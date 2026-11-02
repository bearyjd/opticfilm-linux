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
    property string zoomUrl: ""
    onPreviewsChanged: Qt.callLater(refreshLoaded)

    function refreshLoaded() {
        let ready = 0;
        for (let i = 0; i < frames.count; ++i) {
            const frame = frames.itemAt(i);
            if (frame && frame.ready)
                ready += 1;
        }
        loaded = ready;
    }

    function enlargeFrame(index: int) {
        zoomUrl = previews[index].url;
        enlarged.open();
    }

    property QQC2.Dialog zoomDialog: QQC2.Dialog {
        id: enlarged
        objectName: "enlargedEmptyFrame"
        parent: QQC2.Overlay.overlay
        modal: true
        x: (parent.width - width) / 2
        y: (parent.height - height) / 2
        width: parent.width * 0.9
        height: parent.height * 0.9
        title: qsTr("Inspect empty frame")
        standardButtons: QQC2.Dialog.Close
        contentItem: Image {
            fillMode: Image.PreserveAspectFit
            source: dialog.zoomUrl
        }
    }

    ColumnLayout {
        implicitWidth: Kirigami.Units.gridUnit * 44
        QQC2.Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: qsTr("Check every frame below. Confirm only if all are empty; their raw scans may then be removed.")
        }
        GridLayout {
            Layout.fillWidth: true
            columns: 2
            Repeater {
            id: frames
            model: dialog.previews
            onItemAdded: Qt.callLater(dialog.refreshLoaded)
            onItemRemoved: Qt.callLater(dialog.refreshLoaded)
            delegate: ColumnLayout {
                required property var modelData
                required property int index
                property bool ready: preview.status === Image.Ready && modelData.fingerprint !== ""
                onReadyChanged: Qt.callLater(dialog.refreshLoaded)
                Component.onCompleted: Qt.callLater(dialog.refreshLoaded)
                Layout.fillWidth: true
                QQC2.Label { text: qsTr("Frame %1").arg(modelData.position) }
                Image {
                    id: preview
                    Layout.fillWidth: true
                    Layout.preferredHeight: Kirigami.Units.gridUnit * 11
                    fillMode: Image.PreserveAspectFit
                    source: modelData.url

                }
                QQC2.Button {
                    objectName: "enlargeEmptyFrame" + modelData.position
                    text: qsTr("Enlarge frame %1").arg(modelData.position)
                    enabled: preview.status === Image.Ready
                    onClicked: dialog.enlargeFrame(index)
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
