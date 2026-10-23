import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Kirigami.Dialog {
    id: dialog

    objectName: "orientationDialog"
    title: qsTr("Batch orientation")
    padding: Kirigami.Units.largeSpacing
    standardButtons: Kirigami.Dialog.NoButton
    onOpened: {
        rotationBox.currentIndex = Math.max(0, bridge.rotationChoices.indexOf(bridge.batchRotation));
        mirrorBox.checked = bridge.batchMirror;
    }

    ColumnLayout {
        spacing: Kirigami.Units.largeSpacing
        implicitWidth: Kirigami.Units.gridUnit * 28

        QQC2.Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: qsTr("Turns and flips every slide of the batch, on top of each slide's own rotate and mirror. The mirror is applied first, then the turn.")
        }
        RowLayout {
            QQC2.ComboBox {
                id: rotationBox

                model: bridge.rotationChoices
                displayText: qsTr("%1° clockwise").arg(currentText)
            }
            QQC2.CheckBox {
                id: mirrorBox

                text: qsTr("Mirror")
            }
        }
        QQC2.Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: qsTr("Every slide is enhanced again, and slides you already exported will need exporting again.")
        }
        QQC2.Button {
            Layout.alignment: Qt.AlignRight
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
