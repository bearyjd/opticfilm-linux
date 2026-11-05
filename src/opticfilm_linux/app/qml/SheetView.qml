import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2

ListView {
    id: sheet
    objectName: "sheetView"
    signal reviewFocusRequested()
    signal trayMetaRequested(int number)
    property string lastFocus: ""
    // the tray whose header context menu is open
    property int menuTray: 0
    readonly property real availableCellWidth: Math.max(0, width - 2 * Theme.spacing)
    readonly property int columns: availableCellWidth >= 4 * Theme.minCell + 3 * Theme.spacingSmall ? 4 : 2
    readonly property real cellWidth: Math.min(Theme.maxCell, Math.max(0, (availableCellWidth - (columns - 1) * Theme.spacingSmall) / columns))
    readonly property real captionHeight: Theme.fontBody + 3 * Theme.spacingSmall
    readonly property real cellHeight: cellWidth / 1.5 + captionHeight
    Connections {
        target: bridge
        function onChanged() {
            const next = JSON.stringify(bridge.selectionFocus);
            if (next !== sheet.lastFocus) {
                sheet.lastFocus = next;
                sheet.scrollToFocus();
            }
        }
    }
    clip: true
    model: bridge.sheet
    spacing: Theme.spacing
    topMargin: Theme.spacing
    bottomMargin: Theme.spacing
    reuseItems: true
    boundsBehavior: Flickable.StopAtBounds
    QQC2.ScrollBar.vertical: QQC2.ScrollBar {}

    function scrollToFocus() {
        if (bridge.selectionFocus.length < 2)
            return;
        const row = bridge.sheet.indexOfTray(bridge.selectionFocus[0]);
        if (row >= 0)
            positionViewAtIndex(row, ListView.Contain);
    }

    QQC2.Menu {
        id: trayMenu
        objectName: "trayMenu"
        QQC2.MenuItem {
            objectName: "editTrayMeta"
            text: qsTr("Edit tray description / date…")
            enabled: !bridge.readonly
            onTriggered: sheet.trayMetaRequested(sheet.menuTray)
        }
    }
    function openTrayMenu(number, item) {
        menuTray = number;
        trayMenu.popup(item);
    }

    TapHandler {
        parent: sheet
        onTapped: point => {
            if (!sheet.itemAt(point.position.x + sheet.contentX, point.position.y + sheet.contentY)) {
                bridge.clearSelection();
                sheet.reviewFocusRequested();
            }
        }
    }
    QQC2.Label {
        anchors.centerIn: parent
        width: parent.width - Theme.spacingLarge * 2
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        color: Theme.muted
        visible: sheet.count === 0
        text: qsTr("Your contact sheet will appear here after scanning. New trays appear at the top.")
    }
    delegate: ColumnLayout {
        id: row
        required property int number
        required property string stage
        required property string stageText
        required property string elapsed
        required property string error
        required property string auto
        required property var slides
        width: sheet.width
        spacing: Theme.spacingSmall

        QQC2.ItemDelegate {
            id: header
            function choose(modifiers) {
                bridge.selectTray(row.number, modifiers);
                sheet.reviewFocusRequested();
            }
            objectName: "sheetTray_" + row.number
            Layout.fillWidth: true
            Layout.leftMargin: Theme.spacing
            Layout.rightMargin: Theme.spacing
            text: qsTr("Tray %1 · %2").arg(row.number).arg(row.stageText) + (row.elapsed ? " · " + row.elapsed : "")
            onClicked: choose(0)
            Accessible.description: qsTr("Menu key or right-click: edit tray description / date")
            Keys.onPressed: event => {
                if (event.key === Qt.Key_Space || event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                    choose(event.modifiers);
                    event.accepted = true;
                } else if (event.key === Qt.Key_Menu || (event.key === Qt.Key_F10 && (event.modifiers & Qt.ShiftModifier))) {
                    sheet.openTrayMenu(row.number, header);
                    event.accepted = true;
                }
            }
            MouseArea {
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton | Qt.RightButton
                onClicked: mouse => {
                    if (mouse.button === Qt.RightButton)
                        sheet.openTrayMenu(row.number, header);
                    else
                        header.choose(mouse.modifiers);
                }
            }
        }
        QQC2.Label {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.spacing
            Layout.rightMargin: Theme.spacing
            visible: row.error !== ""
            text: row.error
            color: Theme.text
            wrapMode: Text.Wrap
        }
        GridLayout {
            Layout.alignment: Qt.AlignHCenter
            columns: sheet.columns
            columnSpacing: Theme.spacingSmall
            rowSpacing: Theme.spacingSmall
            Repeater {
                model: row.slides
                delegate: SlideThumb {
                    required property var slideData
                    Layout.preferredWidth: sheet.cellWidth
                    Layout.minimumWidth: sheet.cellWidth
                    Layout.maximumWidth: sheet.cellWidth
                    Layout.preferredHeight: sheet.cellHeight
                    Layout.minimumHeight: sheet.cellHeight
                    Layout.maximumHeight: sheet.cellHeight
                    slide: slideData
                    onChosen: modifiers => {
                        bridge.selectSlide(slide.tray, slide.position, modifiers);
                        sheet.reviewFocusRequested();
                    }
                }
            }
        }
        QQC2.Label {
            Layout.leftMargin: Theme.spacing
            Layout.rightMargin: Theme.spacing
            visible: row.auto !== ""
            Layout.fillWidth: true
            text: row.auto
            color: Theme.muted
            font.pixelSize: Theme.fontSmall
        }
    }
}
