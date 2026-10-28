import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

// One holder slot's default orientation on the new-batch form.
RowLayout {
    id: row

    required property int position
    readonly property int rotation: parseInt(rotationBox.currentText)
    readonly property bool mirrored: mirrorBox.checked

    objectName: "slotOrientation" + position
    Kirigami.FormData.label: qsTr("Holder slot %1:").arg(position)

    QQC2.ComboBox {
        id: rotationBox

        objectName: "slotRotation" + row.position
        model: bridge.rotationChoices
        displayText: qsTr("%1° more").arg(currentText)
    }
    QQC2.CheckBox {
        id: mirrorBox

        objectName: "slotMirror" + row.position
        text: qsTr("Mirror")
    }
}
