import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Kirigami.Dialog {
    id: dialog
    preferredWidth: Math.min(28 * Theme.gu, parent ? parent.width * 0.9 : 28 * Theme.gu)
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
    objectName: "orientationDialog"
    title: qsTr("Batch orientation")
    padding: Theme.spacing
    Binding {
        target: dialog.header
        property: "leftPadding"
        value: dialog.leftPadding
    }
    standardButtons: Kirigami.Dialog.NoButton
    onOpened: {
        rotationBox.currentIndex = Math.max(0, bridge.rotationChoices.indexOf(bridge.batchRotation));
        mirrorBox.checked = bridge.batchMirror;
    }
    ColumnLayout {
        spacing: Theme.spacing
        QQC2.Label {
            color: Theme.text
            font.pixelSize: Theme.fontBody
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: qsTr("Turns every slide of the batch clockwise, then flips it left-right if Mirror is on. Each slide's own rotate and mirror still apply on top, to the image as you see it.")
        }
        RowLayout {
            QQC2.ComboBox {
                id: rotationBox
                objectName: "orientationRotation"
                model: bridge.rotationChoices
                displayText: qsTr("%1° clockwise").arg(currentText)
            }
            QQC2.CheckBox {
                id: mirrorBox
                objectName: "orientationMirror"
                text: qsTr("Mirror")
            }
        }
        QQC2.Label {
            color: Theme.text
            font.pixelSize: Theme.fontBody
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: qsTr("Slides are enhanced again and must be exported again. Trays whose raw scans were removed keep their old orientation; you will be told which. Choosing the current orientation changes nothing.")
        }
    }
    footer: QQC2.Control {
        padding: Theme.spacing
        contentItem: RowLayout {
            Item {
                Layout.fillWidth: true
            }
            PillButton {
                Layout.alignment: Qt.AlignRight
                objectName: "orientationApply"
                text: qsTr("Apply")
                icon.name: "dialog-ok-apply"
                highlighted: true
                enabled: !bridge.readonly
                onClicked: {
                    bridge.setBatchOrientation(parseInt(rotationBox.currentText), mirrorBox.checked);
                    dialog.close();
                }
            }
        }
    }
}
