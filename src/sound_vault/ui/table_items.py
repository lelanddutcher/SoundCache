"""Cells for the small secondary tables (Inbox, Review queues).

These sort by a hidden key so friendly display text ("Nov 9, 11:57 AM", "2,036")
never breaks sorting. The Library table does not use them: it sorts in Python over
its own model, which stays fast at 20k rows.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidgetItem

_SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_SOURCE_LABELS = {"ios_shortcut": "iPhone Shortcut", "manual": "Added by link", "sound_pack": "Sound pack"}
_QUEUE_LABELS = {"packaged_sample": "Packaged samples", "ingested": "Ingested, not reviewed"}


class _SortableItem(QTableWidgetItem):
    """A table cell that sorts by a hidden key (a raw timestamp or a number) when one
    is set, so friendly display text never breaks sorting."""

    def set_sort_key(self, value) -> None:
        self.setData(_SORT_ROLE, value)

    def __lt__(self, other) -> bool:
        mine, theirs = self.data(_SORT_ROLE), other.data(_SORT_ROLE)
        if mine is not None and theirs is not None:
            try:
                return mine < theirs
            except TypeError:
                pass
        # Not super().__lt__: calling the base comparison from a Python override
        # segfaults PySide6 during a sort.
        return self.text().casefold() < other.text().casefold()


def _friendly_time(value: str) -> str:
    """'2026-11-09T16:57:06.645862+00:00' -> 'Nov 9, 11:57 AM' in local time."""
    from datetime import datetime

    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()
    except ValueError:
        return str(value)
    return f"{moment:%b} {moment.day}, {moment.strftime('%I:%M %p').lstrip('0')}"
