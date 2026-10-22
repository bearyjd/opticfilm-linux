"""A list model over plain dicts: roles are the dict keys, rows are replaced by diff."""

from typing import Any

from PySide6.QtCore import QAbstractListModel, QByteArray, QModelIndex, QPersistentModelIndex, Qt

Index = QModelIndex | QPersistentModelIndex
FIRST_ROLE = Qt.ItemDataRole.UserRole + 1


class DictListModel(QAbstractListModel):
    def __init__(self, keys: tuple[str, ...], parent=None) -> None:
        super().__init__(parent)
        self._keys = keys
        self._rows: list[dict[str, Any]] = []

    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802 - Qt override
        return {FIRST_ROLE + i: QByteArray(key.encode()) for i, key in enumerate(self._keys)}

    def rowCount(self, parent: Index = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        slot = role - FIRST_ROLE
        if not index.isValid() or not 0 <= slot < len(self._keys):
            return None
        return self._rows[index.row()].get(self._keys[slot])

    def set_rows(self, rows: list[dict[str, Any]]) -> None:
        """Show ``rows``: only changed rows are signalled when the count is the same."""
        if len(rows) != len(self._rows):
            self.beginResetModel()
            self._rows = list(rows)
            self.endResetModel()
            return
        for i, row in enumerate(rows):
            if row != self._rows[i]:
                self._rows[i] = row
                self.dataChanged.emit(self.index(i), self.index(i))
