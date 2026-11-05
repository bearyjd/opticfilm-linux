import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2

Rectangle {
    id: thumb
    required property var slide
    signal chosen(int modifiers)
    objectName: "slideThumb_" + slide.tray + "_" + slide.position
    color: Theme.card
    radius: Theme.cardRadius
    border.width: slide.focused ? 3 : slide.selected ? 2 : 1
    border.color: slide.selected || slide.focused ? Theme.accent : Theme.line
    opacity: slide.selectable ? 1 : 0.65
    Accessible.role: Accessible.ListItem
    Accessible.name: qsTr("Tray %1, slide %2, %3").arg(slide.tray).arg(slide.position).arg(slide.statusText)
    Accessible.selected: slide.selected

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        enabled: thumb.slide.selectable
        onClicked: mouse => thumb.chosen(mouse.modifiers)
    }
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spacingSmall
        spacing: Theme.spacingSmall
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Image {
                anchors.fill: parent
                asynchronous: true
                fillMode: Image.PreserveAspectFit
                source: thumb.slide.afterUrl || thumb.slide.beforeUrl
            }
            QQC2.Label {
                anchors.centerIn: parent
                visible: !thumb.slide.afterUrl && !thumb.slide.beforeUrl
                objectName: "thumbPlaceholder"
                text: thumb.slide.status === "skipped" ? qsTr("Empty (not scanned)") : thumb.slide.status === "blank" ? qsTr("Looks empty") : qsTr("Preview pending")
                color: Theme.muted
                font.pixelSize: Theme.fontSmall
            }
            QQC2.BusyIndicator {
                anchors.centerIn: parent
                running: thumb.slide.updating
                visible: running
            }
        }
        RowLayout {
            Layout.fillWidth: true
            QQC2.Label {
                text: thumb.slide.position
                color: Theme.muted
                font.bold: true
            }
            QQC2.Label {
                Layout.fillWidth: true
                text: thumb.slide.statusText
                color: Theme.text
                elide: Text.ElideRight
                font.pixelSize: Theme.fontSmall
            }
            QQC2.Label {
                text: "⚑"
                visible: thumb.slide.flags.length > 0
                color: Theme.accent
                Accessible.name: qsTr("Flagged")
            }
        }
    }
}
