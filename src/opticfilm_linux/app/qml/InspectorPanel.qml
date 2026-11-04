import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2

QQC2.ScrollView {
    id: panel
    objectName: "inspectorPanel"
    readonly property var summary: bridge.selectionSummary
    readonly property var slide: bridge.focusedSlide
    readonly property int count: summary.count || 0
    readonly property int singleTray: summary.trays && summary.trays.length === 1 ? summary.trays[0] : -1
    property bool showBefore: false
    signal rescanRequested()
    signal reexportRequested()
    clip: true
    contentWidth: availableWidth
    implicitWidth: 0
    QQC2.ScrollBar.horizontal.policy: QQC2.ScrollBar.AlwaysOff

    function setting(field, fallback) {
        const value = summary.settings ? summary.settings[field] : null;
        return value === null || value === undefined ? fallback : value;
    }

    function mixed(field) {
        return summary.mixed && summary.mixed.indexOf(field) >= 0;
    }

    component SettingSlider: ColumnLayout {
        id: settingControl
        required property string field
        required property string settingField
        required property string label
        property real minimum: 0
        property real maximum: 1
        property real fallback: 0
        spacing: 0
        Layout.fillWidth: true
        QQC2.Label {
            objectName: settingControl.field + "Value"
            Layout.fillWidth: true
            color: Theme.text
            text: settingControl.label + ": " + (panel.mixed(settingControl.settingField) ? qsTr("mixed") : Number(panel.setting(settingControl.settingField, settingControl.fallback)).toFixed(1))
        }
        QQC2.Slider {
            property bool pendingEdit: false
            objectName: settingControl.field + "Slider"
            Layout.fillWidth: true
            from: settingControl.minimum
            to: settingControl.maximum
            stepSize: 0.05
            value: panel.setting(settingControl.settingField, settingControl.fallback)
            Accessible.name: settingControl.label
            onMoved: {
                if (pressed)
                    pendingEdit = true;
                else
                    bridge.adjustSelection(settingControl.field, value);
            }
            onPressedChanged: {
                if (!pressed && pendingEdit) {
                    pendingEdit = false;
                    bridge.adjustSelection(settingControl.field, value);
                }
            }
        }
    }

    ColumnLayout {
        width: panel.availableWidth
        spacing: Theme.spacing
        QQC2.Label {
            Layout.fillWidth: true
            Layout.margins: Theme.spacing
            text: qsTr("INSPECTOR")
            color: Theme.muted
            font.pixelSize: Theme.fontSmall
        }
        QQC2.Label {
            objectName: "selectionCount"
            Layout.fillWidth: true
            Layout.leftMargin: Theme.spacing
            Layout.rightMargin: Theme.spacing
            wrapMode: Text.Wrap
            text: panel.count ? qsTr("%1 selected").arg(panel.count) : qsTr("Select a slide to inspect")
            color: Theme.text
            font.pixelSize: Theme.fontHeading
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.spacing
            Layout.rightMargin: Theme.spacing
            enabled: panel.count > 0 && !bridge.readonly
            PillButton {
                objectName: "acceptSelection"
                Layout.fillWidth: true
                implicitWidth: 5.5 * Theme.gu
                text: qsTr("Accept")
                onClicked: bridge.setSelectionAccepted(true)
            }
            QQC2.Button {
                objectName: "unacceptSelection"
                text: qsTr("Unaccept")
                onClicked: bridge.setSelectionAccepted(false)
            }
        }
        Card {
            objectName: "inspectorPreviewCard"
            Layout.fillWidth: true
            Layout.leftMargin: Theme.spacing
            Layout.rightMargin: Theme.spacing
            visible: panel.slide.position !== undefined
            contentItem: ColumnLayout {
                QQC2.Label {
                    Layout.fillWidth: true
                    text: qsTr("Tray %1 · Slide %2").arg(panel.slide.tray || 0).arg(panel.slide.position || 0)
                    color: Theme.text
                }
                Item {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(panel.width * 0.7, panel.height * Theme.inspectorPreviewFraction)
                    Layout.maximumHeight: panel.height * Theme.inspectorPreviewFraction
                    Image {
                        objectName: "inspectorPreview"
                        anchors.fill: parent
                        asynchronous: true
                        fillMode: Image.PreserveAspectFit
                        source: panel.showBefore ? (panel.slide.beforeUrl || "") : (panel.slide.afterUrl || panel.slide.beforeUrl || "")
                    }
                    QQC2.Label {
                        anchors.centerIn: parent
                        visible: !(panel.showBefore ? panel.slide.beforeUrl : panel.slide.afterUrl || panel.slide.beforeUrl)
                        text: panel.slide.status === "blank" ? qsTr("Looks empty") : qsTr("Preview pending")
                        color: Theme.muted
                    }
                }
                SegmentedControl {
                    objectName: "previewMode"
                    Layout.fillWidth: true
                    model: [qsTr("Before"), qsTr("After")]
                    currentIndex: panel.showBefore ? 0 : 1
                    onActivated: index => panel.showBefore = index === 0
                }
                QQC2.Label {
                    text: panel.slide.statusText || ""
                    color: Theme.muted
                }
                Repeater {
                    model: panel.slide.flags || []
                    QQC2.Label {
                        required property string modelData
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        text: modelData
                        color: Theme.muted
                    }
                }
                QQC2.Button {
                    objectName: "restoreEmptySlide"
                    visible: panel.slide.status === "blank"
                    enabled: !bridge.readonly
                    text: qsTr("Use as slide")
                    onClicked: bridge.setAccepted(panel.slide.tray, panel.slide.position, true)
                }
                QQC2.Button {
                    objectName: "markEmpty_" + (panel.slide.position || 0)
                    text: qsTr("Mark empty")
                    enabled: !bridge.readonly && panel.slide.status !== "blank"
                    onClicked: bridge.markEmpty(panel.slide.tray, panel.slide.position)
                }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.spacing
            Layout.rightMargin: Theme.spacing
            enabled: panel.count > 0 && !bridge.readonly
            spacing: Theme.spacingSmall
            RowLayout {
                QQC2.Button {
                    objectName: "rejectSelection"
                    text: qsTr("Reject")
                    onClicked: bridge.setSelectionStatus("rejected")
                }
                QQC2.Button {
                    objectName: "needsLookSelection"
                    text: qsTr("Needs look")
                    onClicked: bridge.setSelectionStatus("needs_look")
                }
            }
            RowLayout {
                QQC2.Button {
                    objectName: "rotateSelectionLeft"
                    text: "↶"
                    implicitWidth: Theme.controlHeight
                    font.pixelSize: Theme.fontRotation
                    implicitHeight: Theme.controlHeight
                    Accessible.name: qsTr("Rotate left")
                    QQC2.ToolTip.visible: hovered
                    QQC2.ToolTip.text: qsTr("Rotate left")
                    onClicked: bridge.rotateSelection(-1)
                }
                QQC2.Button {
                    objectName: "rotateSelectionRight"
                    text: "↷"
                    implicitWidth: Theme.controlHeight
                    font.pixelSize: Theme.fontRotation
                    implicitHeight: Theme.controlHeight
                    Accessible.name: qsTr("Rotate right")
                    QQC2.ToolTip.visible: hovered
                    QQC2.ToolTip.text: qsTr("Rotate right")
                    onClicked: bridge.rotateSelection(1)
                }
                QQC2.Button {
                    objectName: "mirrorSelection"
                    text: qsTr("Mirror")
                    checkable: false
                    checked: panel.setting("mirror", false)
                    onClicked: bridge.mirrorSelection()
                }
            }
            SettingSlider { field: "brightness"; settingField: "brightness_ev"; label: qsTr("Brightness (EV)"); minimum: -2; maximum: 2 }
            SettingSlider { field: "color"; settingField: "color_strength"; label: qsTr("Colour strength"); fallback: 1 }
            SettingSlider { field: "grain"; settingField: "grain_strength"; label: qsTr("Grain reduction"); fallback: 0.3 }
            SettingSlider { field: "sharpen"; settingField: "sharpen_amount"; label: qsTr("Sharpening"); maximum: 2; fallback: 0.5 }
            QQC2.Button {
                objectName: "resetSelection"
                Layout.fillWidth: true
                text: qsTr("Reset to auto")
                onClicked: bridge.resetSelection()
            }
            QQC2.Button {
                objectName: "reexportSelection"
                Layout.fillWidth: true
                text: qsTr("Re-export selected")
                onClicked: panel.reexportRequested()
            }
        }
        QQC2.Button {
            objectName: "rescanSelectionTray"
            Layout.fillWidth: true
            Layout.leftMargin: Theme.spacing
            Layout.rightMargin: Theme.spacing
            text: qsTr("Re-scan tray…")
            enabled: panel.singleTray > 0 && bridge.canScan && !bridge.readonly
            onClicked: panel.rescanRequested()
        }
        QQC2.Button {
            objectName: "undoSelection"
            Layout.fillWidth: true
            Layout.margins: Theme.spacing
            text: bridge.undoLabel ? qsTr("Undo %1").arg(bridge.undoLabel) : qsTr("Undo")
            enabled: !bridge.readonly && !!bridge.undoLabel
            onClicked: bridge.undo()
        }
    }
}
