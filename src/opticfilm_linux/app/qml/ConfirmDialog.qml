import QtQuick
import org.kde.kirigami as Kirigami

Kirigami.PromptDialog {
    signal confirmed

    standardButtons: Kirigami.Dialog.Yes | Kirigami.Dialog.Cancel
    onAccepted: confirmed()
}
