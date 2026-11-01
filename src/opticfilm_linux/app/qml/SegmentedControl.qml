import QtQuick
import QtQuick.Controls as QQC2

QQC2.Control {
    id: control

    property var model: []
    property int currentIndex: 0
    signal activated(int index)

    implicitWidth: model.length * 96
    implicitHeight: Theme.controlHeight
    padding: 0
    font.pixelSize: Theme.fontBody

    background: Rectangle {
        color: Theme.track
        radius: height / 2
    }
    contentItem: Row {
        spacing: 0
        Repeater {
            id: segments
            model: control.model
            delegate: QQC2.Button {
                id: segment
                required property int index
                required property string modelData
                objectName: "segment" + index
                width: control.availableWidth / control.model.length
                height: control.availableHeight
                text: modelData
                checkable: true
                autoExclusive: true
                checked: index === control.currentIndex
                font: control.font
                Accessible.role: Accessible.RadioButton
                contentItem: Text {
                    text: segment.text
                    font: segment.font
                    color: !segment.enabled ? Theme.muted : segment.checked ? Theme.accentText : Theme.text
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                }
                background: Rectangle {
                    color: segment.checked ? Theme.accent : segment.down ? Theme.line : "transparent"
                    radius: segment.height / 2
                    // Fill the inner half of each end so adjacent segments join squarely.
                    Rectangle {
                        x: segment.index === 0 ? parent.width / 2 : 0
                        width: segment.index === 0 || segment.index === control.model.length - 1 ? parent.width / 2 : parent.width
                        height: parent.height
                        visible: control.model.length > 1
                        color: parent.color
                    }
                }
                Rectangle {
                    objectName: "segmentFocusRing"
                    anchors.fill: parent
                    z: 1
                    visible: segment.visualFocus
                    radius: height / 2
                    color: "transparent"
                    border.width: 2
                    border.color: Theme.text
                    Rectangle {
                        objectName: "segmentFocusInnerRing"
                        anchors.fill: parent
                        anchors.margins: 2
                        radius: height / 2
                        color: "transparent"
                        border.width: 2
                        border.color: segment.checked ? Theme.accentText : Theme.text
                    }
                }
                Keys.onPressed: function(event) {
                    let targetIndex
                    switch (event.key) {
                    case Qt.Key_Left:
                    case Qt.Key_Up:
                        targetIndex = (index + segments.count - 1) % segments.count
                        break
                    case Qt.Key_Right:
                    case Qt.Key_Down:
                        targetIndex = (index + 1) % segments.count
                        break
                    case Qt.Key_Home:
                        targetIndex = 0
                        break
                    case Qt.Key_End:
                        targetIndex = segments.count - 1
                        break
                    default:
                        return
                    }
                    event.accepted = true
                    segments.itemAt(targetIndex).forceActiveFocus(Qt.TabFocusReason)
                    control.activated(targetIndex)
                }
                // Let the owner update currentIndex, preserving its preference binding.
                onClicked: control.activated(index)
            }
        }
    }
}
