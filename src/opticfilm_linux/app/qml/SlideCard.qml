import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Rectangle {
    id: card

    required property int position
    required property string status
    required property string statusText
    required property var flags
    required property bool updating
    required property string afterUrl
    required property string beforeUrl
    property bool current: false
    property bool showBefore: false

    signal chosen

    color: Kirigami.Theme.backgroundColor
    border.width: current ? 3 : 1
    border.color: current ? Kirigami.Theme.highlightColor : Kirigami.Theme.disabledTextColor
    radius: Kirigami.Units.smallSpacing

    TapHandler {
        onTapped: card.chosen()
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Kirigami.Units.smallSpacing
        spacing: Kirigami.Units.smallSpacing

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            Image {
                anchors.fill: parent
                asynchronous: true
                cache: false
                fillMode: Image.PreserveAspectFit
                source: card.status === "blank" ? "" : (card.showBefore ? card.beforeUrl : card.afterUrl)
            }
            QQC2.Label {
                anchors.centerIn: parent
                visible: card.status === "blank"
                text: qsTr("Empty window")
                opacity: 0.6
            }
            QQC2.BusyIndicator {
                anchors.centerIn: parent
                running: card.updating
                visible: card.updating
            }
        }

        RowLayout {
            Layout.fillWidth: true

            Kirigami.Chip {
                text: card.statusText
                checkable: false
                closable: false
                icon.name: card.status === "accepted" ? "dialog-ok" : (card.status === "needs_look" ? "dialog-warning" : "")
            }
            QQC2.Label {
                visible: card.updating
                text: qsTr("updating…")
                opacity: 0.7
            }
            Item {
                Layout.fillWidth: true
            }
            QQC2.Switch {
                visible: card.status !== "blank"
                text: qsTr("Before")
                checked: card.showBefore
                onToggled: card.showBefore = checked
            }
        }

        Repeater {
            model: card.flags

            QQC2.Label {
                required property string modelData

                text: "• " + modelData
                color: Kirigami.Theme.neutralTextColor
                Layout.fillWidth: true
                elide: Text.ElideRight
            }
        }
    }
}
