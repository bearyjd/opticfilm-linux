"""Local popup geometry probe until the shared responsive harness lands."""

from PySide6.QtCore import QRectF


def descendants(item):
    yield item
    for child in item.childItems():
        yield from descendants(child)


def ancestors(item):
    parent = item.parentItem()
    while parent is not None:
        yield parent
        parent = parent.parentItem()


def scene_rect(item):
    return item.mapRectToScene(QRectF(0, 0, item.width(), item.height()))


def popup_rect(dialog):
    rect = QRectF(*(dialog.property(key) for key in ("x", "y", "width", "height")))
    return dialog.property("parent").mapRectToScene(rect)


def assert_inside(rect, container):
    assert rect.width() > 0 and rect.height() > 0, rect
    assert container.adjusted(-1, -1, 1, 1).contains(rect), (rect, container)


def assert_unclipped(item, window):
    rect = scene_rect(item)
    assert_inside(rect, QRectF(0, 0, window.width(), window.height()))
    for parent in ancestors(item):
        if parent.clip():
            assert_inside(rect, scene_rect(parent))


def body_flickable(dialog):
    return next(
        item
        for item in descendants(dialog.property("contentItem"))
        if item.inherits("QQuickFlickable")
    )
