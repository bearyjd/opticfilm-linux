import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

// Which openings the next scan covers. Only created with OPTICFILM_SKIP_EMPTY on; the controller
// refuses any choice the switches do not allow and resets it to all four after each scan.
RowLayout {
    id: selector
    objectName: "scanOpeningsSelector"
    spacing: Theme.spacingSmall
    readonly property var openings: bridge.scanOpenings
    readonly property var counts: [4, 3, 2, 1]
    readonly property bool editable: !bridge.readonly && !bridge.scanPending

    function firstOpenings(count) {
        return Array.from({length: count}, (_, i) => i + 1);
    }
    function toggled(opening) {
        // Always ascending: the driver reads a comma list in the order given.
        return [1, 2, 3, 4].filter(p => p === opening ? openings.indexOf(p) < 0 : openings.indexOf(p) >= 0);
    }

    QQC2.Label {
        text: bridge.skipGapsEnabled ? qsTr("Openings to scan:") : qsTr("Slides in this holder:")
    }
    SegmentedControl {
        objectName: "slidesInHolder"
        visible: !bridge.skipGapsEnabled
        enabled: selector.editable
        model: selector.counts.map(String)
        currentIndex: Math.max(0, selector.counts.indexOf(selector.openings.length))
        Accessible.name: qsTr("Slides in this holder")
        onActivated: index => bridge.setScanOpenings(selector.firstOpenings(selector.counts[index]))
    }
    Repeater {
        model: bridge.skipGapsEnabled ? 4 : 0
        QQC2.Button {
            required property int index
            readonly property int opening: index + 1
            readonly property bool chosen: selector.openings.indexOf(opening) >= 0
            objectName: "openingToggle" + opening
            text: opening
            highlighted: chosen
            // the last chosen opening cannot be cleared: an empty holder is ejected, not scanned
            enabled: selector.editable && !(chosen && selector.openings.length === 1)
            Accessible.role: Accessible.CheckBox
            Accessible.checkable: true
            Accessible.checked: chosen
            Accessible.name: qsTr("Opening %1").arg(opening)
            QQC2.ToolTip.visible: hovered
            QQC2.ToolTip.delay: Kirigami.Units.toolTipDelay
            QQC2.ToolTip.text: chosen ? qsTr("Opening %1 will be scanned. Tap to mark it empty.").arg(opening) : qsTr("Opening %1 is marked empty and will not be scanned.").arg(opening)
            onClicked: bridge.setScanOpenings(selector.toggled(opening))
        }
    }
}
