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
    readonly property bool firstLoadActive: firstLoadCard.active
    readonly property bool firstLoadAvailable: firstLoadCard.available
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
    onHiddenEntryCountChanged: if (hiddenEntryCount === 0)
        expanded = false
    // Entries that need an answer before the user can carry on; a modal drawer must not hide them.
    readonly property int blockingCount: entryKeys.filter(key => key !== "firstLoad" && key !== "auto").length
    property int seenBlockingCount: 0
    signal blockingGained()
    onBlockingCountChanged: {
        if (blockingCount > seenBlockingCount)
            blockingGained();
        seenBlockingCount = blockingCount;
    }
    signal loadHelp()
    signal confirmScanner()
    signal reviewEmptyFrames()

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

    readonly property bool overflowing: contentHeight > availableHeight
    readonly property real minimumStatusHeight: pinnedFooter.implicitHeight
        + Math.min(Theme.controlHeight, statusColumn.implicitHeight)
    Layout.minimumHeight: Math.min(minimumStatusHeight, Layout.maximumHeight)

    // Keep complete entries when they fit; an oversized first entry remains scrollable.
    function heightForBudget(budget) {
        const viewport = Math.max(0, budget - pinnedFooter.implicitHeight);
        if (statusColumn.implicitHeight <= viewport)
            return implicitHeight;
        let completeHeight = 0;
        for (let child of statusColumn.children) {
            if (child.visible && child.y + child.height <= viewport)
                completeHeight = Math.max(completeHeight, child.y + child.height);
        }
        const snapped = completeHeight > 0 ? completeHeight : viewport;
        return Math.min(implicitHeight, pinnedFooter.implicitHeight + snapped);
    }
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
    implicitHeight: statusColumn.implicitHeight + pinnedFooter.implicitHeight
    bottomPadding: pinnedFooter.implicitHeight
    rightPadding: overflowing ? statusBar.width + Theme.spacingSmall : 0
    contentWidth: availableWidth
    contentHeight: statusColumn.implicitHeight
    QQC2.ScrollBar.horizontal.policy: QQC2.ScrollBar.AlwaysOff
    QQC2.ScrollBar.vertical: QQC2.ScrollBar {
        id: statusBar
        objectName: "statusVerticalScrollBar"
        parent: area
        x: area.width - width
        y: area.topPadding
        width: Theme.spacingSmall
        height: area.availableHeight
        orientation: Qt.Vertical
        active: area.overflowing
        minimumSize: Math.min(1, Theme.controlHeight / Math.max(1, height))
        background: Rectangle { color: Theme.track; radius: width / 2 }
        contentItem: Rectangle {
            implicitWidth: Theme.spacingSmall
            implicitHeight: Theme.controlHeight
            color: Theme.muted
            radius: width / 2
        }
        policy: area.overflowing ? QQC2.ScrollBar.AlwaysOn : QQC2.ScrollBar.AlwaysOff
    }

    ColumnLayout {
        id: statusColumn
        width: area.availableWidth
        spacing: 0

        ColumnLayout {
            id: activeFirstLoadSlot
            Layout.fillWidth: true
            Layout.leftMargin: Theme.spacing
            Layout.rightMargin: Theme.spacing
            visible: area.entryVisible("activeFirstLoad")
            FirstLoadCard {
                id: firstLoadCard
                parent: area.firstLoadActive ? activeFirstLoadSlot : idleFirstLoadSlot
                compact: area.shortWindow
                onTerminalHelp: area.loadHelp()
            }
        }
        ErrorBanner {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.spacing
            Layout.rightMargin: Theme.spacing
            visible: area.entryVisible("error")
        }
        ColumnLayout {
            id: heldSlot
            Layout.fillWidth: true
            Layout.margins: Theme.spacingSmall
            Layout.leftMargin: Theme.spacing
            Layout.rightMargin: Theme.spacing
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
            Layout.margins: Theme.spacingSmall
            Layout.leftMargin: Theme.spacing
            Layout.rightMargin: Theme.spacing
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
            Layout.margins: Theme.spacingSmall
            Layout.leftMargin: Theme.spacing
            Layout.rightMargin: Theme.spacing
            visible: area.entryVisible("latch")
            QQC2.Label {
                objectName: "holderLatchText"
                Layout.fillWidth: true
                visible: text !== "" && !bridge.holderLatchWarning
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
            Layout.leftMargin: Theme.spacing
            Layout.rightMargin: Theme.spacing
            visible: area.entryVisible("firstLoad")
        }
        ColumnLayout {
            id: autoSlot
            Layout.fillWidth: true
            Layout.margins: Theme.spacingSmall
            Layout.leftMargin: Theme.spacing
            Layout.rightMargin: Theme.spacing
            visible: area.entryVisible("auto")
            QQC2.Label {
                objectName: "autoPipelineStatus"
                visible: text !== ""
                parent: bridge.emptyConfirmationCount > 0 ? emptyTextSlot : bridge.heldTray > 0 ? heldTextSlot : autoSlot
                Layout.fillWidth: true
                Layout.row: 0
                wrapMode: Text.Wrap
                text: bridge.emptyConfirmationCount > 0 ? qsTr("%1 frame(s) look empty - confirm they are empty to free raw").arg(bridge.emptyConfirmationCount) : bridge.autoStatus
            }
        }
    }

    // Keep the hidden safety entries reachable below the scrolled entries.
    ColumnLayout {
        id: pinnedFooter
        parent: area
        x: 0
        y: area.height - height
        width: area.width
        z: 1
        spacing: 0
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingSmall
            QQC2.Button {
                objectName: "statusMore"
                visible: area.hiddenEntryCount > 0
                flat: true
                Layout.fillWidth: true
                text: area.expanded ? qsTr("Show less") : qsTr("%1 more: %2").arg(area.hiddenEntryCount).arg(area.entryKeys.slice(area.entryLimit).map(key => area.entryTitle(key)).join(", "))
                onClicked: area.expanded = !area.expanded
            }
            QQC2.Label {
                objectName: "statusMoreBelow"
                visible: area.overflowing
                opacity: area.contentItem.atYEnd ? 0 : 1
                Layout.rightMargin: Theme.spacing
                horizontalAlignment: Text.AlignHCenter
                color: Theme.muted
                font.pixelSize: Theme.fontSmall
                text: qsTr("More below ↓")
            }
        }
        GridLayout {
            id: scannerRow
            columns: area.compact ? 1 : 5
            // the scanner's status and the holder controls: own row, so the text is never cut off
            Layout.fillWidth: true
            Layout.margins: Theme.spacingSmall / 2
            Layout.leftMargin: Theme.spacing
            Layout.rightMargin: Theme.spacing
            columnSpacing: Theme.spacing
            rowSpacing: Theme.spacingSmall

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

    }
}
