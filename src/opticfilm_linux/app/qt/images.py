"""Serves the batch's preview JPEGs to QML as ``image://preview/<tray>/<position>/<kind>/<token>``.

The event-driven token changes the URL when a preview changes. Images are decoded on
Qt's image loading thread, including previews shown in confirmation dialogs.
"""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider

PreviewLookup = Callable[[int, int, str], Path | None]


class PreviewProvider(QQuickImageProvider):
    def __init__(self, lookup: PreviewLookup) -> None:
        super().__init__(
            QQuickImageProvider.ImageType.Image,
            QQuickImageProvider.Flag.ForceAsynchronousImageLoading,
        )
        self._lookup = lookup

    def requestImage(self, id: str, size: QSize, requestedSize: QSize) -> QImage:  # noqa: A002, N802, N803
        parts = id.split("/")
        try:
            path = self._lookup(int(parts[0]), int(parts[1]), parts[2])
        except (ValueError, IndexError):
            return QImage()
        image = QImage(str(path)) if path is not None else QImage()
        if not image.isNull():
            size.setWidth(image.width())
            size.setHeight(image.height())
        return image
