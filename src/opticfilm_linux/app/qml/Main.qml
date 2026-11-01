import QtQuick
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Kirigami.ApplicationWindow {
    id: root

    property bool closeWhenIdle: false

    title: bridge.hasBatch ? bridge.batchName + " — OpticFilm Linux" : "OpticFilm Linux"
    width: 1220
    height: 780
    visible: true
    color: Theme.window

    Binding { target: Theme; property: "dark"; value: bridge.theme === "dark" }
    WindowPalette { window: root; colors: bridge.paletteColors }

    Kirigami.Theme.inherit: false
    Kirigami.Theme.backgroundColor: Theme.window
    Kirigami.Theme.alternateBackgroundColor: Theme.card
    Kirigami.Theme.textColor: Theme.text
    Kirigami.Theme.disabledTextColor: Theme.muted
    Kirigami.Theme.highlightColor: Theme.accent
    Kirigami.Theme.highlightedTextColor: Theme.accentText
    Kirigami.Theme.linkColor: Theme.accent

    footer: QQC2.ToolBar {
        implicitHeight: themeToggle.implicitHeight + Theme.spacingSmall * 2
        background: Rectangle { color: Theme.window }
        SegmentedControl {
            id: themeToggle
            objectName: "themeToggle"
            anchors.right: parent.right
            anchors.rightMargin: Theme.spacing
            anchors.verticalCenter: parent.verticalCenter
            model: [qsTr("Dark"), qsTr("Light")]
            currentIndex: bridge.theme === "dark" ? 0 : 1
            Accessible.name: qsTr("Color theme")
            onActivated: function(index) { bridge.setTheme(index === 0 ? "dark" : "light"); }
        }
    }

    pageStack.globalToolBar.style: Kirigami.ApplicationHeaderStyle.None
    pageStack.initialPage: Kirigami.Page {
        padding: 0

        Loader {
            anchors.fill: parent
            sourceComponent: bridge.hasBatch ? mainComponent : startComponent
        }
    }

    Component {
        id: mainComponent

        MainPage {}
    }
    Component {
        id: startComponent

        StartPage {}
    }

    Connections {
        target: bridge

        function onChanged() {
            if (root.closeWhenIdle && !bridge.scanPending)
                root.close();
        }
    }

    onClosing: function (close) {
        if (bridge.closeRequested() === "scan_running") {
            close.accepted = false;
            waitDialog.open();
        }
    }

    Kirigami.PromptDialog {
        id: waitDialog

        title: qsTr("A scan is running")
        subtitle: qsTr("A scan cannot be stopped safely. Wait for it to finish and then close, or keep working.")
        standardButtons: Kirigami.Dialog.NoButton
        customFooterActions: [
            Kirigami.Action {
                text: qsTr("Wait, then close")
                icon.name: "chronometer"
                onTriggered: {
                    root.closeWhenIdle = true;
                    waitDialog.close();
                }
            },
            Kirigami.Action {
                text: qsTr("Keep working")
                icon.name: "dialog-cancel"
                onTriggered: waitDialog.close()
            }
        ]
    }
}
