import QtQuick
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import "../../src/opticfilm_linux/app/qml"

QQC2.ApplicationWindow {
    id: root
    width: 1100
    height: 900
    visible: true
    color: Theme.window
    Kirigami.Theme.inherit: false
    Kirigami.Theme.backgroundColor: Theme.window
    Kirigami.Theme.alternateBackgroundColor: Theme.card
    Kirigami.Theme.textColor: Theme.text
    Kirigami.Theme.disabledTextColor: Theme.muted
    Kirigami.Theme.highlightColor: Theme.accent
    Kirigami.Theme.highlightedTextColor: Theme.accentText
    Kirigami.Theme.linkColor: Theme.accent

    property int confirmations: 0
    property int rejections: 0

    Binding {
        target: Theme
        property: "dark"
        value: bridge.theme === "dark"
    }
    WindowPalette {
        window: root
        colors: bridge.paletteColors
    }

    function labelColors(item) {
        let colors = [];
        if ((item instanceof QQC2.Label) || (item instanceof Kirigami.SelectableLabel))
            colors.push(item.color.toString());
        for (const child of item.children)
            colors = colors.concat(labelColors(child));
        return colors;
    }

    function showsIcon(button) {
        return button.contentItem.children.some(child => child.source !== undefined && child.visible && child.source.toString() !== "");
    }

    function pillButtons(item) {
        let buttons = [];
        for (const child of item.children) {
            if (child.objectName === "pillFocusRing")
                return [item];
            buttons = buttons.concat(pillButtons(child));
        }
        return buttons;
    }

    StartPage {
        anchors.fill: parent
    }
    ExportDialog {
        objectName: "sampleExport"
    }
    FreeSpaceDialog {
        objectName: "sampleFreeSpace"
    }
    OrientationDialog {
        objectName: "sampleOrientation"
    }
    LoadHelpDialog {
        objectName: "sampleLoadHelp"
    }
    EmptyFramesDialog {
        objectName: "emptyFramesDialog"
    }
    ConfirmDialog {
        objectName: "sampleConfirm"
        title: "Confirm action"
        subtitle: "Keep the confirmation text legible."
        onConfirmed: root.confirmations += 1
        onRejected: root.rejections += 1
    }
}
