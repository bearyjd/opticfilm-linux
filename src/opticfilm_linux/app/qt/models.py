"""A list model over plain dicts: roles are the dict keys, rows are replaced by diff."""

from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    Slot,
)

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
        if (
            not index.isValid()
            or not 0 <= index.row() < len(self._rows)
            or not 0 <= slot < len(self._keys)
        ):
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


class SheetModel(DictListModel):
    """Keep tray identities stable as newer trays arrive at the top of the sheet."""

    def set_rows(self, rows: list[dict[str, Any]]) -> None:
        wanted = {row["number"] for row in rows}
        for index in range(len(self._rows) - 1, -1, -1):
            if self._rows[index]["number"] not in wanted:
                self.beginRemoveRows(QModelIndex(), index, index)
                del self._rows[index]
                self.endRemoveRows()
        existing = {row["number"] for row in self._rows}
        for index, row in enumerate(rows):
            current = (
                index
                if index < len(self._rows) and self._rows[index]["number"] == row["number"]
                else self.indexOfTray(row["number"])
                if row["number"] in existing
                else -1
            )
            if current < 0:
                self.beginInsertRows(QModelIndex(), index, index)
                self._rows.insert(index, row)
                self.endInsertRows()
                existing.add(row["number"])
            else:
                if current != index:
                    self._move_row(current, index)
                self._update_row(index, row)

    def _move_row(self, current: int, target: int) -> None:
        destination = target if target < current else target + 1
        self.beginMoveRows(QModelIndex(), current, current, QModelIndex(), destination)
        self._rows.insert(target, self._rows.pop(current))
        self.endMoveRows()

    def _update_row(self, index: int, row: dict[str, Any]) -> None:
        previous = self._rows[index]
        roles = [
            FIRST_ROLE + offset
            for offset, key in enumerate(self._keys)
            if previous.get(key) != row.get(key)
        ]
        if roles:
            self._rows[index] = row
            self.dataChanged.emit(self.index(index), self.index(index), roles)

    @Slot(int, result=int)
    def indexOfTray(self, number: int) -> int:  # noqa: N802
        return next((i for i, row in enumerate(self._rows) if row["number"] == number), -1)

    @Slot(int, result="QVariantMap")
    def get(self, index: int) -> dict:
        from copy import deepcopy

        return deepcopy(self._rows[index]) if 0 <= index < len(self._rows) else {}
