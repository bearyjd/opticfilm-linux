import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

// Saved workflow switches. All are off on a fresh profile. An OPTICFILM_* environment variable
// decides over the saved value, and then its switch is locked here.
Kirigami.Dialog {
    id: dialog
    objectName: "workflowSettingsDialog"
    title: qsTr("Workflow settings")
    preferredWidth: Math.min(32 * Theme.gu, parent ? parent.width * 0.9 : 32 * Theme.gu)
    maximumWidth: preferredWidth
    width: preferredWidth
    maximumHeight: parent ? parent.height * 0.9 : Infinity
    padding: Theme.spacing
    standardButtons: Kirigami.Dialog.Close

    Kirigami.Theme.inherit: false
    Kirigami.Theme.backgroundColor: Theme.card
    Kirigami.Theme.alternateBackgroundColor: Theme.track
    Kirigami.Theme.textColor: Theme.text
    Kirigami.Theme.disabledTextColor: Theme.muted
    Kirigami.Theme.highlightColor: Theme.accent
    Kirigami.Theme.highlightedTextColor: Theme.accentText
    Kirigami.Theme.linkColor: Theme.accent

    background: Rectangle {
        objectName: "dialogSurface"
        radius: Theme.cardRadius
        color: Theme.card
        border.color: Theme.line
    }

    component Setting: ColumnLayout {
        id: setting
        property alias switchObject: toggle
        property string label
        property string detail
        property string lockedBy: ""
        property bool value: false
        signal toggled(bool on)
        Layout.fillWidth: true
        spacing: Theme.spacingSmall / 2

        QQC2.Switch {
            id: toggle
            Layout.fillWidth: true
            text: setting.label
            checked: setting.value
            enabled: setting.lockedBy === ""
            onToggled: setting.toggled(checked)
        }
        QQC2.Label {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.spacing
            wrapMode: Text.Wrap
            color: Theme.muted
            font.pixelSize: Theme.fontBody
            text: setting.detail
        }
        QQC2.Label {
            objectName: setting.objectName + "Locked"
            Layout.fillWidth: true
            Layout.leftMargin: Theme.spacing
            wrapMode: Text.Wrap
            color: Theme.warn
            font.pixelSize: Theme.fontBody
            visible: setting.lockedBy !== ""
            text: qsTr("Set by %1. Unset it and restart the app to change this here.").arg(setting.lockedBy)
        }
    }

    ColumnLayout {
        spacing: Theme.spacing

        Setting {
            objectName: "autoExportSetting"
            label: qsTr("Export automatically in new batches")
            detail: qsTr("The starting choice when you create a batch. Batches you already have keep their own setting. Automatic export is not yet proven on the scanner.")
            value: bridge.autoExportDefault
            onToggled: on => bridge.setAutoExportDefault(on)
            switchObject.objectName: "autoExportDefaultSwitch"
        }
        Setting {
            objectName: "autoEjectSetting"
            label: qsTr("Eject the holder after each good scan")
            detail: qsTr("Applies to the next scan that finishes. Automatic eject is not yet proven on the scanner; watch the first few trays.")
            value: bridge.autoEjectEnabled
            lockedBy: bridge.autoEjectLockedBy
            onToggled: on => bridge.setAutoEject(on)
            switchObject.objectName: "autoEjectSwitch"
        }
        Setting {
            objectName: "skipEmptySetting"
            label: qsTr("Choose which holder openings to scan")
            detail: qsTr("Lets you skip empty openings. Applies from the next Scan press; a scan already started is not changed. Not yet tested on the scanner.")
            value: bridge.skipEmptyEnabled
            lockedBy: bridge.skipEmptyLockedBy
            onToggled: on => bridge.setSkipEmpty(on)
            switchObject.objectName: "skipEmptySwitch"
        }
    }
}
