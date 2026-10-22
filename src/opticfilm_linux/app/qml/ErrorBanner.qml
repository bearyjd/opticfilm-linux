import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

ColumnLayout {
    id: banner

    property bool showDetail: false

    spacing: 0

    Kirigami.InlineMessage {
        Layout.fillWidth: true
        visible: bridge.hasBanner
        type: Kirigami.MessageType.Error
        text: bridge.bannerMessage
        onVisibleChanged: if (!visible) banner.showDetail = false
        onLinkActivated: link => Qt.openUrlExternally(link)
        actions: [
            Kirigami.Action {
                text: banner.showDetail ? qsTr("Hide details") : qsTr("Details")
                onTriggered: banner.showDetail = !banner.showDetail
            },
            Kirigami.Action {
                text: qsTr("Dismiss")
                icon.name: "dialog-close"
                onTriggered: bridge.dismissBanner()
            },
            Kirigami.Action {
                visible: bridge.bannerLocked
                text: qsTr("Open read-only")
                icon.name: "document-open"
                onTriggered: bridge.openLastReadonly()
            }
        ]
    }

    QQC2.Label {
        Layout.fillWidth: true
        Layout.margins: Kirigami.Units.smallSpacing
        visible: bridge.hasBanner && banner.showDetail
        text: bridge.bannerDetail.replace(/^LOCKED: /, "")
        wrapMode: Text.Wrap
        font.family: "monospace"
        opacity: 0.8
    }
}
