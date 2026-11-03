import QtQuick
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

// Pane owns content geometry directly, keeping the padding around custom content.
QQC2.Pane {
    padding: Theme.spacing
    property string text: ""
    // severity cards (warning / error) override these with a Theme.warn / Theme.error accent
    property color borderColor: Theme.line
    property int borderWidth: 1

    // Give nested Kirigami controls the same color context as the surface.
    Kirigami.Theme.inherit: false
    Kirigami.Theme.backgroundColor: Theme.card
    Kirigami.Theme.alternateBackgroundColor: Theme.track
    Kirigami.Theme.textColor: Theme.text
    Kirigami.Theme.disabledTextColor: Theme.muted
    Kirigami.Theme.highlightColor: Theme.accent
    Kirigami.Theme.highlightedTextColor: Theme.accentText
    Kirigami.Theme.linkColor: Theme.accent

    background: Rectangle {
        radius: Theme.cardRadius
        color: Theme.card
        border.color: borderColor
        border.width: borderWidth
    }
}
