import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Kirigami.Page {
    id: page

    property int currentSlide: 1

    objectName: "mainPage"
    padding: 0
    title: bridge.batchName

    // Keyboard use for the review loop.
    Keys.onLeftPressed: currentSlide = Math.max(1, currentSlide - 1)
    Keys.onRightPressed: currentSlide = Math.min(4, currentSlide + 1)
    Keys.onSpacePressed: page.toggleAccepted()
    Keys.onPressed: event => {
        if (event.key === Qt.Key_BracketLeft)
            bridge.rotate(bridge.selectedTray, currentSlide, -1);
        else if (event.key === Qt.Key_BracketRight)
            bridge.rotate(bridge.selectedTray, currentSlide, 1);
        else if (event.key === Qt.Key_M && !bridge.readonly && bridge.selectedTray >= 0)
            bridge.mirror(bridge.selectedTray, currentSlide);
    }
    focus: true

    function toggleAccepted() {
        if (bridge.readonly || bridge.selectedTray < 0)
            return;
        for (let i = 0; i < slideRepeater.count; ++i) {
            const card = slideRepeater.itemAt(i);
            if (card && card.position === currentSlide && card.status !== "blank")
                bridge.setAccepted(bridge.selectedTray, currentSlide, card.status !== "accepted");
        }
    }

    ConfirmDialog {
        id: rescanDialog

        title: qsTr("Re-scan tray %1?").arg(bridge.selectedTray)
        subtitle: qsTr("The old scan is kept on disk, but this tray's slide settings and choices are reset and it is enhanced again.")
        onConfirmed: bridge.rescan(bridge.selectedTray)
    }
    ExportDialog {
        id: exportDialog
    }
    FreeSpaceDialog {
        id: freeSpaceDialog
    }
    OrientationDialog {
        id: orientationDialog
    }

    header: QQC2.ToolBar {
        RowLayout {
            anchors.fill: parent
            spacing: Kirigami.Units.largeSpacing

            QQC2.Button {
                text: bridge.scanPending ? qsTr("Scanning…") : qsTr("Scan")
                icon.name: "scanner"
                highlighted: true
                enabled: bridge.canScan
                onClicked: bridge.scan()
            }
            QQC2.Label {
                Layout.fillWidth: true
                text: bridge.readonly ? qsTr("Read-only — another program has this batch open") : bridge.scannerText
                elide: Text.ElideRight
            }
            QQC2.Button {
                text: qsTr("Accept all")
                icon.name: "dialog-ok-apply"
                enabled: !bridge.readonly && bridge.trays.rowCount() > 0
                onClicked: bridge.acceptAll()
            }
            QQC2.Button {
                text: qsTr("Batch orientation…")
                icon.name: "object-rotate-right"
                enabled: !bridge.readonly
                onClicked: orientationDialog.open()
            }
            QQC2.Button {
                text: qsTr("Export…")
                icon.name: "document-export"
                enabled: !bridge.readonly && bridge.trays.rowCount() > 0
                onClicked: exportDialog.open()
            }
            QQC2.Button {
                text: qsTr("Free disk space…")
                icon.name: "edit-clear-all"
                enabled: !bridge.readonly
                onClicked: {
                    bridge.requestFreeSpace();
                    freeSpaceDialog.open();
                }
            }
            QQC2.Button {
                text: qsTr("Close batch")
                icon.name: "document-close"
                onClicked: bridge.closeBatch()
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        ErrorBanner {
            Layout.fillWidth: true
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            TrayQueue {
                Layout.preferredWidth: Kirigami.Units.gridUnit * 16
                Layout.fillHeight: true
            }
            Kirigami.Separator {
                Layout.fillHeight: true
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.margins: Kirigami.Units.largeSpacing
                spacing: Kirigami.Units.largeSpacing

                RowLayout {
                    visible: bridge.selectedTray >= 0
                    Kirigami.Heading {
                        level: 2
                        text: qsTr("Tray %1").arg(bridge.selectedTray)
                        Layout.fillWidth: true
                    }
                    QQC2.Button {
                        text: qsTr("Re-scan this tray…")
                        icon.name: "view-refresh"
                        enabled: bridge.canScan
                        onClicked: rescanDialog.open()
                    }
                }

                QQC2.Label {
                    visible: bridge.selectedTray < 0
                    Layout.alignment: Qt.AlignCenter
                    opacity: 0.7
                    text: qsTr("Select a tray to see its slides.")
                }

                GridLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    columns: 2
                    rowSpacing: Kirigami.Units.smallSpacing
                    columnSpacing: Kirigami.Units.smallSpacing

                    Repeater {
                        id: slideRepeater

                        model: bridge.slides

                        delegate: SlideCard {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Layout.preferredHeight: 1
                            current: position === page.currentSlide
                            onChosen: page.currentSlide = position
                        }
                    }
                }

                Item {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Kirigami.Units.gridUnit * 11
                    visible: bridge.selectedTray >= 0

                    Repeater {
                        model: bridge.slides

                        delegate: AdjustPanel {
                            anchors.fill: parent
                            visible: position === page.currentSlide
                            tray: bridge.selectedTray
                        }
                    }
                }
            }
        }
    }
}
