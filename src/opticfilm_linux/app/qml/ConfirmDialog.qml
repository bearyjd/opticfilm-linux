import QtQuick
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Kirigami.PromptDialog {
    id: dialog
    padding: Theme.spacing
    contentPadding: Theme.spacing

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
    // PromptDialog's internal View context is independent of the popup's palette.
    Binding {
        target: dialog.contentItem.Kirigami.Theme
        property: "textColor"
        value: Theme.text
    }
    Binding {
        target: dialog.contentItem.Kirigami.Theme
        property: "backgroundColor"
        value: Theme.card
    }

    signal confirmed
    standardButtons: Kirigami.Dialog.Yes | Kirigami.Dialog.Cancel
    onAccepted: confirmed()

    // Enter activates the accept button: it takes the focus when the dialog opens.
    onAboutToShow: Qt.callLater(focusAccept)
    function focusAccept() {
        for (let i = 0; i < buttonBox.count; ++i) {
            if (!buttonBox.itemAt(i).rejecting) {
                buttonBox.itemAt(i).forceActiveFocus(Qt.TabFocusReason);
                break;
            }
        }
    }

    footer: QQC2.DialogButtonBox {
        id: buttonBox
        standardButtons: dialog.standardButtons
        padding: Theme.spacing
        spacing: Theme.spacingSmall
        background: Item {}
        delegate: PillButton {
            readonly property bool rejecting: QQC2.DialogButtonBox.buttonRole === QQC2.DialogButtonBox.RejectRole
            flat: rejecting
            highlighted: !rejecting
            icon.name: rejecting ? "dialog-cancel" : "dialog-ok"
        }
    }
}
