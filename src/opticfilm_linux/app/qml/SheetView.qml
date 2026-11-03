import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2

ListView {
    id: sheet
    objectName: "sheetView"
    signal reviewFocusRequested()
    property string lastFocus: ""
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

    function placeholder(tray, position, text, busy) {
        return {tray: tray, position: position, status: "pending", statusText: text,
                flags: [], selected: false, focused: false, selectable: false,
                beforeUrl: "", afterUrl: "", updating: busy};
    }

    function scrollToFocus() {
        if (bridge.selectionFocus.length < 2)
            return;
        const row = bridge.sheet.indexOfTray(bridge.selectionFocus[0]);
        if (row >= 0)
            positionViewAtIndex(row, ListView.Contain);
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
        width: sheet.width - Theme.spacing * 2
        x: Theme.spacing
        spacing: Theme.spacingSmall

        QQC2.ItemDelegate {
            objectName: "sheetTray_" + row.number
            Layout.fillWidth: true
            text: qsTr("Tray %1 · %2").arg(row.number).arg(row.stageText) + (row.elapsed ? " · " + row.elapsed : "")
            onClicked: {
                bridge.selectTray(row.number, headerMouse.modifiers);
                sheet.reviewFocusRequested();
            }
            MouseArea {
                id: headerMouse
                property int modifiers: 0
                anchors.fill: parent
                onClicked: mouse => {
                    bridge.selectTray(row.number, mouse.modifiers);
                    sheet.reviewFocusRequested();
                }
            }
        }
        QQC2.Label {
            Layout.fillWidth: true
            visible: row.error !== ""
            text: row.error
            color: Theme.text
            wrapMode: Text.Wrap
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingSmall
            Repeater {
                model: row.slides.length ? row.slides : [1, 2, 3, 4].map(p => sheet.placeholder(row.number, p, row.stageText, row.stage === "scanning" || row.stage === "enhancing"))
                delegate: SlideThumb {
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    Layout.preferredHeight: Math.max(130, Math.min(200, sheet.width * 0.28))
                    slide: modelData
                    onChosen: modifiers => {
                        bridge.selectSlide(slide.tray, slide.position, modifiers);
                        sheet.reviewFocusRequested();
                    }
                }
            }
        }
        QQC2.Label {
            visible: row.auto !== ""
            Layout.fillWidth: true
            text: row.auto
            color: Theme.muted
            font.pixelSize: Theme.fontSmall
        }
    }
}
