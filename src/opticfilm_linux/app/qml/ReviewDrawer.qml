import QtQuick
import org.kde.kirigami as Kirigami

// Modal side drawer that hosts one review pane; closing it hands focus back to the review.
Kirigami.OverlayDrawer {
    id: drawer

    // The page that owns the review (provides width and focusReview()).
    property Item host

    modal: true
    focus: true
    handleVisible: false
    handle.parent: host
    handle.visible: false
    width: Theme.drawerWidth(host ? host.width : 0)
    contentItem: Item {}
    onClosed: if (host) host.focusReview()
}
