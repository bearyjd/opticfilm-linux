import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

QQC2.ScrollView {
    id: area
    objectName: "statusScroll"
    property bool shortWindow: false
    property bool compact: false
    property bool expanded: false
    readonly property int entryLimit: shortWindow ? 1 : 3
    readonly property bool firstLoadActive: ["jogging", "await_reinsert", "loading"].indexOf(bridge.loadPhase) >= 0
    readonly property bool firstLoadAvailable: bridge.canFirstLoad || firstLoadActive || bridge.loadPhase === "failed" || bridge.loadPhase === "done"
    readonly property var entryKeys: [
        {key: "activeFirstLoad", present: firstLoadAvailable && firstLoadActive},
        {key: "error", present: bridge.hasBanner},
        {key: "held", present: bridge.heldTray > 0},
        {key: "empty", present: bridge.emptyConfirmationCount > 0},
        {key: "latch", present: bridge.holderLatchText !== "" || bridge.holderLatchWarning || bridge.canConfirmHolderLatched},
        {key: "firstLoad", present: firstLoadAvailable && !firstLoadActive},
        {key: "auto", present: bridge.heldTray <= 0 && bridge.emptyConfirmationCount <= 0 && (bridge.batchAutoExport || bridge.autoStatus !== "")}
    ].filter(entry => entry.present).map(entry => entry.key)
    readonly property int hiddenEntryCount: Math.max(0, entryKeys.length - entryLimit)
    readonly property int visibleEntryCount: expanded ? entryKeys.length : Math.min(entryLimit, entryKeys.length)
    readonly property alias themeHost: compactThemeHost
    signal loadHelp()
    signal confirmScanner()
    signal reviewEmptyFrames()
    signal shortcutHelp()

    function entryVisible(key) {
        const index = entryKeys.indexOf(key);
        return index >= 0 && (expanded || index < entryLimit);
    }

    function entryTitle(key) {
        return {activeFirstLoad: qsTr("First load"), error: qsTr("Error"),
            held: qsTr("Raw cleanup held"), empty: qsTr("Empty frames"),
            latch: qsTr("Holder latch"), firstLoad: qsTr("First load"),
            auto: qsTr("Auto status")}[key];
    }

    Layout.minimumHeight: 0
    function reflowScannerActions() {
        const target = compact ? scannerActions : scannerRow;
        const actions = [loadHelpLink, confirmScannerButton, ejectButton, loadNextHost];
        actions.forEach(action => action.parent = null);
        actions.forEach(action => action.parent = target);
    }

    onCompactChanged: Qt.callLater(reflowScannerActions)
    Component.onCompleted: reflowScannerActions()

    padding: 0
    clip: true
    implicitHeight: statusColumn.implicitHeight
    contentWidth: availableWidth
    contentHeight: statusColumn.implicitHeight
    QQC2.ScrollBar.horizontal.policy: QQC2.ScrollBar.AlwaysOff

    ColumnLayout {
        id: statusColumn
        width: area.availableWidth
        spacing: 0
        GridLayout {
            id: scannerRow
            columns: area.compact ? 1 : 5
            // the scanner's status and the holder controls: own row, so the text is never cut off
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.smallSpacing
            columnSpacing: Kirigami.Units.largeSpacing
            rowSpacing: Kirigami.Units.smallSpacing

            QQC2.Label {
                objectName: "scannerStatusText"
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: bridge.readonly ? qsTr("Read-only — another program has this batch open") : bridge.scannerText
            }
            Flow {
                id: scannerActions
                visible: area.compact
                Layout.fillWidth: true
                spacing: Theme.spacingSmall
            }
            QQC2.Button {
                id: loadHelpLink
                objectName: "loadHelpLink"
                visible: !bridge.readonly && bridge.scannerStatus === "not_ready"
                flat: true
                text: qsTr("How to load the scanner")
                icon.name: "help-contextual"
                onClicked: area.loadHelp()
            }
            QQC2.Button {
                id: confirmScannerButton
                objectName: "confirmScannerButton"
                visible: !bridge.readonly && bridge.scannerStatus === "interrupted"
                enabled: bridge.canConfirmScanner
                text: qsTr("Confirm scanner reset…")
                icon.name: "dialog-ok"
                onClicked: area.confirmScanner()
            }
            QQC2.Button {
                id: ejectButton
                objectName: "ejectButton"
                implicitHeight: Math.round(Theme.gu * 40 / 18)
                text: qsTr("Eject holder")
                icon.name: "media-eject"
                enabled: bridge.canEject
                onClicked: bridge.ejectHolder()
            }
            Item {
                id: loadNextHost
                // a disabled button gets no hover, so the explanation hangs on this wrapper
                implicitWidth: loadNextButton.implicitWidth
                implicitHeight: loadNextButton.implicitHeight
                QQC2.ToolTip.visible: loadNextHover.hovered
                QQC2.ToolTip.delay: Kirigami.Units.toolTipDelay
                QQC2.ToolTip.text: bridge.nextStripEnabled ? qsTr("Load the next holder after the first terminal load.") : qsTr("Switched off by OPTICFILM_NEXT_STRIP. Unset it and restart the app to enable loading the next holder.") + " " + qsTr("If the scanner was just switched on, use the terminal steps instead; the driver refuses this and nothing is sent.")

                HoverHandler {
                    id: loadNextHover
                }
                QQC2.Button {
                    id: loadNextButton

                    objectName: "loadNextButton"
                    implicitHeight: Math.round(Theme.gu * 40 / 18)
                    anchors.fill: parent
                    text: qsTr("Load next holder")
                    icon.name: "go-down"
                    enabled: bridge.canLoadNext
                    onClicked: bridge.loadNextHolder()
                }
            }
        }

        ColumnLayout {
            id: activeFirstLoadSlot
            Layout.fillWidth: true
            visible: area.entryVisible("activeFirstLoad")
            FirstLoadCard {
                parent: area.firstLoadActive ? activeFirstLoadSlot : idleFirstLoadSlot
                compact: area.shortWindow
                onTerminalHelp: area.loadHelp()
            }
        }
        ErrorBanner {
            Layout.fillWidth: true
            visible: area.entryVisible("error")
        }
        ColumnLayout {
            id: heldSlot
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.smallSpacing
            visible: area.entryVisible("held")
            ColumnLayout {
                id: heldTextSlot
                Layout.fillWidth: true
            }
            QQC2.Label {
                Layout.fillWidth: true
                visible: bridge.emptyConfirmationCount > 0
                wrapMode: Text.Wrap
                text: bridge.autoStatus
            }
            QQC2.Button {
                objectName: "freeHeldRaw"
                visible: bridge.heldTray > 0 && !bridge.readonly
                text: qsTr("Approve settings and release raw cleanup")
                onClicked: bridge.requestHeldRelease(bridge.heldTray)
            }
        }
        ColumnLayout {
            id: emptySlot
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.smallSpacing
            visible: area.entryVisible("empty")
            ColumnLayout {
                id: emptyTextSlot
                Layout.fillWidth: true
            }
            QQC2.Button {
                objectName: "confirmEmptyFrames"
                visible: bridge.emptyConfirmationTray > 0 && !bridge.readonly
                text: qsTr("Review %1 empty frames in tray %2").arg(bridge.emptyConfirmationCount).arg(bridge.emptyConfirmationTray)
                onClicked: area.reviewEmptyFrames()
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.smallSpacing
            visible: area.entryVisible("latch")
            QQC2.Label {
                objectName: "holderLatchText"
                Layout.fillWidth: true
                visible: text !== ""
                wrapMode: Text.Wrap
                text: bridge.holderLatchText
            }
            Kirigami.InlineMessage {
                Layout.fillWidth: true
                visible: bridge.holderLatchWarning
                type: Kirigami.MessageType.Warning
                text: qsTr("Holder latch is unknown. Reseat and latch the holder before scanning.")
            }
            QQC2.Button {
                objectName: "confirmHolderLatched"
                visible: bridge.canConfirmHolderLatched
                text: qsTr("Confirm holder is seated and latched")
                onClicked: bridge.confirmHolderLatched()
            }
        }
        ColumnLayout {
            id: idleFirstLoadSlot
            Layout.fillWidth: true
            visible: area.entryVisible("firstLoad")
        }
        ColumnLayout {
            id: autoSlot
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.smallSpacing
            visible: area.entryVisible("auto")
            QQC2.Label {
                objectName: "autoPipelineStatus"
                parent: bridge.emptyConfirmationCount > 0 ? emptyTextSlot : bridge.heldTray > 0 ? heldTextSlot : autoSlot
                Layout.fillWidth: true
                Layout.row: 0
                wrapMode: Text.Wrap
                text: bridge.emptyConfirmationCount > 0 ? qsTr("%1 frame(s) look empty - confirm they are empty to free raw").arg(bridge.emptyConfirmationCount) : bridge.autoStatus
            }
        }
        QQC2.Button {
            objectName: "statusMore"
            visible: area.hiddenEntryCount > 0
            flat: true
            Layout.fillWidth: true
            text: area.expanded ? qsTr("Show less") : qsTr("%1 more: %2").arg(area.hiddenEntryCount).arg(area.entryKeys.slice(area.entryLimit).map(key => area.entryTitle(key)).join(", "))
            onClicked: area.expanded = !area.expanded
        }
        Flow {
            Layout.fillWidth: true
            visible: area.shortWindow
            spacing: Theme.spacingSmall
            Item {
                id: compactThemeHost
                implicitWidth: 10 * Theme.gu
                implicitHeight: Theme.controlHeight
            }
            QQC2.Button {
                objectName: "shortcutHelp"
                text: qsTr("?")
                Accessible.name: qsTr("Keyboard shortcuts")
                onClicked: area.shortcutHelp()
            }
        }
    }
}
