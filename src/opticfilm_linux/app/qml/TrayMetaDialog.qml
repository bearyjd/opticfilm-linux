import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

// "Edit tray description / date…": the tray's own values; its slides inherit them unless edited.
Kirigami.Dialog {
    id: dialog
    objectName: "trayMetaDialog"
    property int tray: 0
    property var meta: ({})
    property string error: ""
    preferredWidth: Math.min(32 * Theme.gu, parent ? parent.width * 0.9 : 32 * Theme.gu)
    maximumWidth: preferredWidth
    width: preferredWidth
    maximumHeight: parent ? parent.height * 0.9 : Infinity

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
    title: qsTr("Tray %1: description and date").arg(tray)
    padding: Theme.spacing
    Binding {
        target: dialog.header
        property: "leftPadding"
        value: dialog.leftPadding
    }
    standardButtons: Kirigami.Dialog.NoButton
    onOpened: {
        error = "";
        descriptionField.text = meta.description || "";
        dateField.setDate(meta.date || "");
        descriptionField.forceActiveFocus();
    }

    // keep the comparison values current if the batch default changes while the dialog is open
    Connections {
        target: bridge
        enabled: dialog.opened
        function onChanged() {
            const row = bridge.sheet.get(bridge.sheet.indexOfTray(dialog.tray));
            if (row.meta)
                dialog.meta = row.meta;
        }
    }

    function levelText(level) {
        return level === "tray" ? qsTr("set for this tray") : level === "batch" ? qsTr("from batch") : qsTr("not set");
    }

    // Only fields the owner changed are written; each is its own undo step.
    function save() {
        let refusal = "";
        if (descriptionField.text !== (meta.description || ""))
            refusal = bridge.setTrayMeta(tray, "description", descriptionField.text);
        if (refusal === "" && dateField.dateText !== (meta.date || ""))
            refusal = bridge.setTrayMeta(tray, "date", dateField.dateText);
        error = refusal;
        if (refusal === "")
            close();
    }

    function resetBoth() {
        let refusal = "";
        for (const field of ["description", "date"]) {
            const level = field === "description" ? meta.descriptionLevel : meta.dateLevel;
            if (refusal === "" && level === "tray")
                refusal = bridge.resetTrayMeta(tray, field);
        }
        error = refusal;
        if (refusal === "")
            close();
    }

    ColumnLayout {
        spacing: Theme.spacingSmall
        QQC2.Label {
            text: qsTr("Description (%1):").arg(dialog.levelText(dialog.meta.descriptionLevel))
            color: Theme.text
        }
        QQC2.TextField {
            id: descriptionField
            objectName: "trayMetaDescription"
            Layout.fillWidth: true
            maximumLength: 500
            placeholderText: qsTr("e.g. Lake trip, Grandpa's 70th")
            Accessible.name: qsTr("Tray description")
            onAccepted: if (dateField.dateProblem === "") dialog.save()
        }
        QQC2.Label {
            text: qsTr("Date, an estimate (%1):").arg(dialog.levelText(dialog.meta.dateLevel))
            color: Theme.text
        }
        EstimatedDateField {
            id: dateField
            namePrefix: "trayMeta"
        }
        QQC2.Label {
            objectName: "trayMetaError"
            Layout.fillWidth: true
            visible: text !== ""
            text: dateField.showDateProblem ? dateField.dateProblem : dialog.error
            Accessible.role: Accessible.AlertMessage
            Accessible.name: text
            color: Kirigami.Theme.negativeTextColor
            wrapMode: Text.WordWrap
        }
        QQC2.Label {
            Layout.fillWidth: true
            text: qsTr("Slides of this tray use these unless a slide has its own.")
            color: Theme.muted
            font.pixelSize: Theme.fontSmall
            wrapMode: Text.WordWrap
        }
    }
    footer: QQC2.Control {
        padding: Theme.spacing
        contentItem: RowLayout {
            PillButton {
                objectName: "trayMetaReset"
                text: qsTr("Reset to batch")
                Accessible.name: qsTr("Reset the tray description and date to the batch values")
                enabled: !bridge.readonly && (dialog.meta.descriptionLevel === "tray" || dialog.meta.dateLevel === "tray")
                onClicked: dialog.resetBoth()
            }
            Item {
                Layout.fillWidth: true
            }
            PillButton {
                objectName: "trayMetaCancel"
                text: qsTr("Cancel")
                onClicked: dialog.close()
            }
            PillButton {
                objectName: "trayMetaSave"
                text: qsTr("Save")
                icon.name: "dialog-ok-apply"
                highlighted: true
                enabled: !bridge.readonly && dateField.dateProblem === ""
                onClicked: dialog.save()
            }
        }
    }
}
