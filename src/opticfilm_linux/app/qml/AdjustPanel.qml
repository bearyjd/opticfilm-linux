import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Kirigami.FormLayout {
    id: panel

    required property int tray
    required property int position
    required property string status
    required property bool mirror
    required property real brightness
    required property real color
    required property real grain
    required property real sharpen
    required property bool ownerDecided

    enabled: !bridge.readonly && status !== "blank"

    RowLayout {
        Kirigami.FormData.label: qsTr("Slide %1").arg(panel.position)

        QQC2.Switch {
            text: qsTr("Accepted")
            checked: panel.status === "accepted"
            onToggled: bridge.setAccepted(panel.tray, panel.position, checked)
        }
        QQC2.Button {
            text: qsTr("Rotate left")
            icon.name: "object-rotate-left"
            QQC2.ToolTip.text: qsTr("Rotate left ( [ )")
            QQC2.ToolTip.visible: hovered
            onClicked: bridge.rotate(panel.tray, panel.position, -1)
        }
        QQC2.Button {
            text: qsTr("Rotate right")
            icon.name: "object-rotate-right"
            QQC2.ToolTip.text: qsTr("Rotate right ( ] )")
            QQC2.ToolTip.visible: hovered
            onClicked: bridge.rotate(panel.tray, panel.position, 1)
        }
        QQC2.Button {
            text: qsTr("Mirror")
            icon.name: "object-flip-horizontal"
            checkable: true
            checked: panel.mirror
            QQC2.ToolTip.text: qsTr("Flip left-right ( m )")
            QQC2.ToolTip.visible: hovered
            onToggled: {
                bridge.mirror(panel.tray, panel.position);
                checked = Qt.binding(() => panel.mirror);
            }
        }
    }

    QQC2.Slider {
        Kirigami.FormData.label: qsTr("Brightness (EV):")
        from: -2
        to: 2
        stepSize: 0.1
        value: panel.brightness
        onMoved: bridge.adjust(panel.tray, panel.position, "brightness", value)
    }
    QQC2.Slider {
        Kirigami.FormData.label: qsTr("Colour strength:")
        from: 0
        to: 1
        stepSize: 0.05
        value: panel.color
        onMoved: bridge.adjust(panel.tray, panel.position, "color", value)
    }
    QQC2.Slider {
        Kirigami.FormData.label: qsTr("Grain reduction:")
        from: 0
        to: 1
        stepSize: 0.05
        value: panel.grain
        onMoved: bridge.adjust(panel.tray, panel.position, "grain", value)
    }
    QQC2.Slider {
        Kirigami.FormData.label: qsTr("Sharpening:")
        from: 0
        to: 2
        stepSize: 0.05
        value: panel.sharpen
        onMoved: bridge.adjust(panel.tray, panel.position, "sharpen", value)
    }
    QQC2.Button {
        text: qsTr("Reset to auto")
        icon.name: "edit-undo"
        enabled: panel.ownerDecided
        onClicked: bridge.resetSlide(panel.tray, panel.position)
    }
}
