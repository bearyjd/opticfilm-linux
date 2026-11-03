import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Card {
    id: banner
    objectName: "errorBanner"

    property bool showDetail: false

    borderColor: Theme.error
    borderWidth: 2
    visible: bridge.hasBanner
    onVisibleChanged: if (!visible)
        showDetail = false
    Accessible.role: Accessible.AlertMessage
    Accessible.name: bridge.bannerMessage

    contentItem: ColumnLayout {
        spacing: Theme.spacingSmall

        RowLayout {
            spacing: Theme.spacingSmall

            Kirigami.Icon {
                source: "dialog-error"
                color: Theme.error
                Layout.preferredWidth: Theme.iconMedium
                Layout.preferredHeight: Theme.iconMedium
                Accessible.ignored: true
            }
            Kirigami.SelectableLabel {
                objectName: "bannerMessage"
                Layout.fillWidth: true
                text: bridge.bannerMessage
                color: Theme.text
                font.pixelSize: Theme.fontBody
                wrapMode: Text.Wrap
                onLinkActivated: link => Qt.openUrlExternally(link)
            }
        }
        RowLayout {
            spacing: Theme.spacingSmall

            PillButton {
                objectName: "bannerDetails"
                text: banner.showDetail ? qsTr("Hide details") : qsTr("Details")
                icon.name: "dialog-information"
                onClicked: banner.showDetail = !banner.showDetail
            }
            PillButton {
                objectName: "bannerDismiss"
                text: qsTr("Dismiss")
                flat: true
                icon.name: "dialog-close"
                onClicked: bridge.dismissBanner()
            }
            PillButton {
                objectName: "bannerReadonly"
                visible: bridge.bannerLocked
                text: qsTr("Open read-only")
                onClicked: bridge.openLastReadonly()
            }
        }
        QQC2.Label {
            objectName: "bannerDetail"
            Layout.fillWidth: true
            visible: banner.showDetail
            text: bridge.bannerDetail.replace(/^LOCKED: /, "")
            color: Theme.muted
            font.pixelSize: Theme.fontBody
            font.family: "monospace"
            wrapMode: Text.Wrap
        }
    }
}
