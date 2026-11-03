import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Kirigami.Dialog {
    id: dialog
    preferredWidth: Math.min(44 * Kirigami.Units.gridUnit, parent ? parent.width * 0.9 : 44 * Kirigami.Units.gridUnit)
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
    padding: Theme.spacing
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
        padding: Theme.spacing
        background: Rectangle {
            objectName: "dialogSurface"
            radius: Theme.cardRadius
            color: Theme.card
            border.color: Theme.line
        }
        header: QQC2.Label {
            text: enlarged.title
            color: Theme.text
            font.pixelSize: Theme.fontHeading
            font.weight: Font.DemiBold
            padding: Theme.spacing
        }
        footer: QQC2.DialogButtonBox {
            standardButtons: enlarged.standardButtons
            padding: Theme.spacing
            spacing: Theme.spacingSmall
            background: Item {}
            delegate: PillButton {
                icon.name: "dialog-close"
                highlighted: true
            }
        }
        contentItem: Image {
            fillMode: Image.PreserveAspectFit
            source: dialog.zoomUrl
        }
    }

    ColumnLayout {
        spacing: Theme.spacing
        QQC2.Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: Theme.text
            font.pixelSize: Theme.fontBody
            text: qsTr("Check every frame below. Confirm only if all are empty; their raw scans may then be removed.")
        }
        GridLayout {
            id: frameGrid
            Layout.fillWidth: true
            columns: width >= 2 * 14 * Kirigami.Units.gridUnit ? 2 : 1
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
                    QQC2.Label {
                        color: Theme.text
                        font.pixelSize: Theme.fontBody
                        text: qsTr("Frame %1").arg(modelData.position)
                    }
                    Image {
                        id: preview
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.min(Kirigami.Units.gridUnit * 8, width / 1.5)
                        fillMode: Image.PreserveAspectFit
                        source: modelData.url
                    }
                    PillButton {
                        objectName: "enlargeEmptyFrame" + modelData.position
                        text: qsTr("Enlarge frame %1").arg(modelData.position)
                        icon.name: "zoom-in"
                        enabled: preview.status === Image.Ready
                        onClicked: dialog.enlargeFrame(index)
                    }
                }
            }
        }
        QQC2.Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: Theme.muted
            font.pixelSize: Theme.fontBody
            visible: dialog.previews.some(item => item.fingerprint === "" || item.url === "")
            text: qsTr("Some empty frames have no current scan evidence or preview. Rescan this tray to review and confirm them.")
        }
    }
    footer: QQC2.Control {
        id: footer
        padding: Theme.spacing
        contentItem: GridLayout {
            columns: footer.availableWidth >= confirmButton.implicitWidth + cancelButton.implicitWidth + columnSpacing ? 2 : 1
            columnSpacing: Theme.spacingSmall
            PillButton {
                id: confirmButton
                Layout.alignment: Qt.AlignRight
                objectName: "confirmReviewedEmptyFrames"
                text: qsTr("Confirm all these frames are empty")
                icon.name: "dialog-ok-apply"
                highlighted: true
                enabled: dialog.previews.length > 0 && dialog.loaded === dialog.previews.length
                onClicked: {
                    bridge.confirmEmptyFrames(dialog.tray, dialog.sweepVersion, dialog.previews);
                    dialog.close();
                }
            }
            PillButton {
                id: cancelButton
                Layout.alignment: Qt.AlignRight
                text: qsTr("Cancel")
                flat: true
                icon.name: "dialog-cancel"
                onClicked: dialog.close()
            }
        }
    }
}
