import QtQuick
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Kirigami.ApplicationWindow {
    id: root

    readonly property real gridUnit: Theme.gu
    property int windowedWidth: 0
    property int windowedHeight: 0
    signal saveWindowGeometry(int windowWidth, int windowHeight, bool maximized)

    // Previous windowed size: a maximized size can arrive before visibility flips,
    // so it is rolled back when the window turns out to be maximized/fullscreen.
    property int previousWindowedWidth: 0
    property int previousWindowedHeight: 0

    onWidthChanged: if (visibility === Window.Windowed && width > 0) {
        previousWindowedWidth = windowedWidth;
        windowedWidth = width;
    }
    onHeightChanged: if (visibility === Window.Windowed && height > 0) {
        previousWindowedHeight = windowedHeight;
        windowedHeight = height;
    }
    onVisibilityChanged: {
        if (visibility === Window.Windowed) {
            if (windowedWidth > 0 && windowedHeight > 0) {
                width = windowedWidth;
                height = windowedHeight;
            }
        } else if (visibility === Window.Maximized || visibility === Window.FullScreen) {
            if (width === windowedWidth && previousWindowedWidth > 0)
                windowedWidth = previousWindowedWidth;
            if (height === windowedHeight && previousWindowedHeight > 0)
                windowedHeight = previousWindowedHeight;
        }
    }

    property bool closeWhenIdle: false
    property bool firstLoadQuitConfirmed: false

    title: bridge.hasBatch ? bridge.batchName + " — OpticFilm Linux" : "OpticFilm Linux"
    width: 68 * gridUnit
    height: 43 * gridUnit
    visible: false
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

    // Overlay plus page padding keeps page.height stable when the footer hides.
    // A layout footer would feed its own height back into the short-window breakpoint.
    readonly property real themeFooterHeight: themeFooter.implicitHeight

    QQC2.ToolBar {
        id: themeFooter
        parent: root.contentItem
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        z: 1
        visible: !bridge.hasBatch || !pageLoader.item || !pageLoader.item.shortWindow
        implicitHeight: themeToggle.implicitHeight + Theme.spacingSmall * 2
        background: Rectangle { color: Theme.window }
        SegmentedControl {
            id: themeToggle
            objectName: "themeToggle"
            parent: themeFooter
            anchors.right: parent.right
            anchors.rightMargin: parent === themeFooter ? Theme.spacing : 0
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
            id: pageLoader
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

        StartPage { bottomPadding: root.themeFooterHeight }
    }

    Connections {
        target: bridge

        function onChanged() {
            if (root.closeWhenIdle && !bridge.scanPending)
                root.close();
        }
    }

    onClosing: function (close) {
        const decision = bridge.closeRequested();
        if (decision === "first_load_running" && !root.firstLoadQuitConfirmed) {
            close.accepted = false;
            firstLoadQuitDialog.open();
        } else if (decision === "scan_running") {
            close.accepted = false;
            waitDialog.open();
        }
        if (close.accepted)
            root.saveWindowGeometry(root.windowedWidth, root.windowedHeight,
                                    root.visibility === Window.Maximized);
    }

    ConfirmDialog {
        id: firstLoadQuitDialog
        objectName: "firstLoadQuitDialog"
        title: qsTr("Quit during first load?")
        subtitle: qsTr("Cancelling needs a power cycle afterwards. Switch the scanner off until the lights go out, then switch it on before loading again.")
        onConfirmed: {
            root.firstLoadQuitConfirmed = true;
            root.close();
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
