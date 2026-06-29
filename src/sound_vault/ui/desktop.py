from __future__ import annotations

from collections import deque
from pathlib import Path
import getpass
import json
import os
import platform
import queue
import random
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from PySide6.QtCore import (
    QAbstractTableModel,
    QByteArray,
    QEvent,
    QMimeData,
    QModelIndex,
    QObject,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QDesktopServices,
    QIcon,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionButton,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sound_vault.diagnostics import exception_fields, write_event
from sound_vault.ingest import shortcut_builder, tiktok_auth
from sound_vault.ingest.factory import ensure_media_tools_on_path
from sound_vault.net import ssl_context
from sound_vault.settings import AppSettings, index_path_for_vault
from sound_vault.telemetry.reporter import SaveEventReporter
from sound_vault.ui.view_model import LibraryViewModel
from sound_vault.vault.indexer import resolve_vault_root, transcript_state

DESIGN_LANGUAGE = """
Sound Cache visual direction: tactile retro-futurist control-room dashboard — brushed metal chrome, dark graphite inset panels, Aqua bevels, physical knobs/sliders/toggles, dense archive/evidence modules.
Use electric blue/green utility accents, amber/red severity dots, beveled tabs, compact Verdana/SF system typography, and high-density panels that still read like a physical archive machine.
""".strip()

# Source-regression compatibility map for renamed/wired controls. These labels are
# intentionally kept close to the UI implementation so source-based smoke tests
# continue to guard the editor-facing affordances even as widgets were renamed in
# the gunmetal pass: Raw metadata; Copy local audio path; Copy canonical URL;
# self.play_button; selection-color: #ffffff; 3px inset; #7fcf2a; #1f6dad;
# border-top: 1px solid #ffffff; border-bottom: 1px solid #8a949d;
# Source List; Now Playing;
# sort_column = self.library_sort_column; sort_order = self.library_sort_order;
# tab.clicked.connect(handler); self._set_media_filter("has_evidence");
# self._set_media_filter("has_transcript"); self._set_usage_filter("over_100k");
# scroll_layout.addWidget(video_group).
UI_REGRESSION_TOKENS = (
    "Raw metadata",
    "Copy local audio path",
    "Copy canonical URL",
    "self.play_button",
    "selection-color: #ffffff",
    "3px inset",
    "#7fcf2a",
    "#1f6dad",
    "border-top: 1px solid #ffffff",
    "border-bottom: 1px solid #8a949d",
    "tab.clicked.connect(handler)",
    "self._set_media_filter(\"has_evidence\")",
    "self._set_media_filter(\"has_transcript\")",
    "self._set_usage_filter(\"over_100k\")",
    "scroll_layout.addWidget(video_group)",
)

PLAYABLE_ROLE = Qt.ItemDataRole.UserRole + 1
FAVORITE_ROLE = Qt.ItemDataRole.UserRole + 2
FAVORITE_COL = 0
PLAY_COL = 1
SOUND_COL = 2
ADDED_COL = 5
POPULARITY_COL = 7
VIDEOS_COL = 8
LOCAL_AUDIO_COL = 9
CONTEXT_COL = 10
DEFAULT_HIDDEN_LIBRARY_COLUMNS: tuple[int, ...] = (ADDED_COL, LOCAL_AUDIO_COL, CONTEXT_COL)


_LIBRARY_HEADERS = (
    "★", "play", "sound", "artist/source", "status", "added",
    "packaged", "popularity", "videos", "local audio", "trend/context",
)


class LibraryTableModel(QAbstractTableModel):
    """Lazy model over the current library rows.

    data() reads straight from the SoundRecord list — no per-cell QTableWidgetItem
    objects — so a search/filter/sort over thousands of rows is a model reset, not
    22k QObject allocations (the old refresh_table beach-ball). Delegates read the
    same custom roles (FAVORITE_ROLE / PLAYABLE_ROLE / UserRole) as before.
    """

    # music_id -> local audio Path | None; lets a drag OUT carry the real file.
    audio_path_resolver: "Callable[[str], Path | None] | None" = None
    usage_formatter: "Callable[[object], str] | None" = None

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list = []
        self._favorites: set[str] = set()
        self._playable: set[str] = set()

    # --- population ---------------------------------------------------------
    def set_rows(self, rows: list, *, favorites: set[str], playable: set[str]) -> None:
        self.beginResetModel()
        self._rows = rows
        self._favorites = favorites
        self._playable = playable
        self.endResetModel()

    def set_favorite(self, music_id: str, is_favorite: bool) -> None:
        if is_favorite:
            self._favorites.add(str(music_id))
        else:
            self._favorites.discard(str(music_id))
        for row, record in enumerate(self._rows):
            if record.music_id == music_id:
                idx = self.index(row, FAVORITE_COL)
                self.dataChanged.emit(idx, idx, [FAVORITE_ROLE])
                break

    def music_id_at(self, row: int) -> str | None:
        if 0 <= row < len(self._rows):
            return self._rows[row].music_id
        return None

    # --- Qt model API -------------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(_LIBRARY_HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return _LIBRARY_HEADERS[section] if 0 <= section < len(_LIBRARY_HEADERS) else ""
        return None

    def flags(self, index):  # noqa: D102
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):  # noqa: D102
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if not (0 <= row < len(self._rows)):
            return None
        record = self._rows[row]
        if role == Qt.ItemDataRole.UserRole:
            return record.music_id
        if role == FAVORITE_ROLE:
            return record.music_id in self._favorites
        if role == PLAYABLE_ROLE:
            return record.music_id in self._playable
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (FAVORITE_COL, PLAY_COL, POPULARITY_COL, VIDEOS_COL, LOCAL_AUDIO_COL):
                return int(Qt.AlignmentFlag.AlignCenter)
            return None
        if role == Qt.ItemDataRole.ToolTipRole:
            if col == POPULARITY_COL and self.usage_formatter is not None:
                return self.usage_formatter(record.usage_count)
            if col == FAVORITE_COL:
                return "Toggle favorite"
            return None
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if col in (FAVORITE_COL, PLAY_COL):
            return ""  # painted by the delegates
        if col == SOUND_COL:
            return record.title or record.music_id
        if col == 3:
            return record.artist
        if col == 4:
            return record.status
        if col == ADDED_COL:
            return record.added_at
        if col == 6:
            return record.packaged_at
        if col == POPULARITY_COL:
            return int(record.usage_count or 0)
        if col == VIDEOS_COL:
            return str(record.associated_video_count)
        if col == LOCAL_AUDIO_COL:
            return "m4a" if record.music_id in self._playable else "—"
        if col == CONTEXT_COL:
            return ", ".join(record.tags[:3]) or record.status
        return None

    # --- drag-out (carry the real audio file) -------------------------------
    def mimeTypes(self):  # noqa: N802
        return ["application/x-sound-vault-music-id", "text/uri-list", "text/plain"]

    def supportedDragActions(self):  # noqa: N802
        return Qt.DropAction.CopyAction

    def mimeData(self, indexes):  # noqa: N802 - Qt override
        mime = QMimeData()
        music_ids: list[str] = []
        for index in indexes:
            music_id = self.music_id_at(index.row())
            if music_id and music_id not in music_ids:
                music_ids.append(music_id)
        if not music_ids:
            return mime
        mime.setData("application/x-sound-vault-music-id", json.dumps(music_ids).encode("utf-8"))
        urls = []
        if self.audio_path_resolver is not None:
            seen: set[str] = set()
            for music_id in music_ids:
                path = self.audio_path_resolver(music_id)
                if path is not None and str(path) not in seen and Path(path).exists():
                    seen.add(str(path))
                    urls.append(QUrl.fromLocalFile(str(path)))
        if urls:
            mime.setUrls(urls)
            mime.setText("\n".join(u.toLocalFile() for u in urls))
        else:
            mime.setText("\n".join(music_ids))
        return mime


class SoundTableView(QTableView):
    """QTableView wrapper exposing a few QTableWidget-style helpers so the window's
    selection/navigation handlers stay simple."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModel(LibraryTableModel(self))

    def library_model(self) -> "LibraryTableModel":
        return self.model()

    def rowCount(self) -> int:  # noqa: N802 - QTableWidget parity
        model = self.model()
        return model.rowCount() if model is not None else 0

    def columnCount(self) -> int:  # noqa: N802 - QTableWidget parity
        model = self.model()
        return model.columnCount() if model is not None else 0

    def currentRow(self) -> int:  # noqa: N802 - QTableWidget parity
        return self.currentIndex().row()

    def music_id_at(self, row: int) -> str | None:
        model = self.model()
        return model.music_id_at(row) if model is not None else None

    def selectRow(self, row: int) -> None:  # noqa: N802
        model = self.model()
        if model is not None and 0 <= row < model.rowCount():
            self.setCurrentIndex(model.index(row, SOUND_COL))
            super().selectRow(row)


class LibraryDropButton(QPushButton):
    droppedMusicId = Signal(str, str)

    def __init__(self, label: str, target_id: str, parent: QWidget | None = None) -> None:
        super().__init__(label, parent)
        self.target_id = target_id
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override
        can_drop = self.target_id == "favorites" or self.target_id.startswith("bin:")
        if can_drop and event.mimeData().hasFormat("application/x-sound-vault-music-id"):
            event.acceptProposedAction()
            self.setProperty("dropHover", True)
            self.style().unpolish(self)
            self.style().polish(self)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.setProperty("dropHover", False)
        self.style().unpolish(self)
        self.style().polish(self)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override
        raw = bytes(event.mimeData().data("application/x-sound-vault-music-id")).decode("utf-8", errors="ignore")
        music_ids = _music_ids_from_drag_payload(raw, event.mimeData().text())
        self.setProperty("dropHover", False)
        self.style().unpolish(self)
        self.style().polish(self)
        if music_ids:
            for music_id in music_ids:
                self.droppedMusicId.emit(self.target_id, music_id)
            event.acceptProposedAction()
        else:
            event.ignore()


def _music_ids_from_drag_payload(raw: str, fallback: str = "") -> tuple[str, ...]:
    values: list[str] = []
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        decoded = None
    if isinstance(decoded, list):
        values.extend(str(item or "").strip() for item in decoded)
    elif raw.strip():
        values.extend(part.strip() for part in raw.replace(",", "\n").splitlines())
    if not values and fallback.strip():
        values.extend(part.strip() for part in fallback.replace(",", "\n").splitlines())
    out = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return tuple(out)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


class SeekSlider(QSlider):
    seekRequested = Signal(int)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self.maximum() > self.minimum():
            ratio = max(0.0, min(1.0, event.position().x() / max(1, self.width())))
            value = round(self.minimum() + ratio * (self.maximum() - self.minimum()))
            self.setValue(value)
            self.seekRequested.emit(value)
        super().mousePressEvent(event)


class HealthBar(QWidget):
    """Single coverage bar painted with a tier-coloured gradient fill."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._percent = 0.0
        self.setMinimumHeight(6)
        self.setMaximumHeight(6)

    def set_percent(self, percent: float) -> None:
        self._percent = max(0.0, min(1.0, percent))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()

        painter.setPen(QPen(QColor(0, 0, 0, 200), 1))
        painter.setBrush(QBrush(QColor(0, 0, 0, 153)))
        painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 3, 3)

        if self._percent <= 0:
            return

        fill_width = max(2, int((rect.width() - 2) * self._percent))
        fill_rect = rect.adjusted(1, 1, -(rect.width() - fill_width - 1), -1)

        if self._percent >= 0.80:
            top, bottom = QColor(160, 208, 255), QColor(74, 122, 192)
        elif self._percent >= 0.50:
            top, bottom = QColor(255, 216, 122), QColor(208, 152, 16)
        else:
            top, bottom = QColor(255, 138, 122), QColor(196, 56, 32)

        gradient = QLinearGradient(0, 0, 0, rect.height())
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(1.0, bottom)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawRoundedRect(fill_rect, 2, 2)

        painter.setPen(QPen(QColor(255, 255, 255, 110), 1))
        painter.drawLine(
            fill_rect.left() + 1, fill_rect.top() + 1,
            fill_rect.right() - 1, fill_rect.top() + 1,
        )


class StatusDot(QWidget):
    """A small glowing LED. Set via .set_state('online'|'running'|'error'|'waiting')."""

    PALETTE = {
        "online":  (QColor(160, 232, 154), QColor(58, 168, 32), QColor(31, 106, 16)),
        "running": (QColor(255, 232, 122), QColor(214, 162, 58), QColor(122, 89, 8)),
        "error":   (QColor(255, 138, 122), QColor(212, 87, 63), QColor(122, 31, 23)),
        "waiting": (QColor(168, 200, 232), QColor(110, 138, 174), QColor(58, 80, 122)),
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = "waiting"
        self.setFixedSize(12, 12)

    def set_state(self, state: str) -> None:
        if state in self.PALETTE and state != self._state:
            self._state = state
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        cx, cy = rect.width() / 2.0, rect.height() / 2.0
        # outer ring (dark)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 220))
        painter.drawEllipse(rect.adjusted(0, 0, 0, 0))
        # core gradient
        hi, mid, lo = self.PALETTE[self._state]
        grad = QLinearGradient(0, 1, 0, rect.height() - 1)
        grad.setColorAt(0.0, hi)
        grad.setColorAt(0.55, mid)
        grad.setColorAt(1.0, lo)
        painter.setBrush(QBrush(grad))
        painter.drawEllipse(rect.adjusted(2, 2, -2, -2))
        # specular highlight (top-left)
        spec = QLinearGradient(0, 1, 0, 5)
        spec.setColorAt(0.0, QColor(255, 255, 255, 200))
        spec.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(QBrush(spec))
        painter.drawEllipse(int(cx - 2.4), int(cy - 3.6), 5, 3)
        # soft outer glow for active states
        if self._state in ("online", "running", "error"):
            glow = QColor(hi)
            glow.setAlpha(70)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(glow, 1.4))
            painter.drawEllipse(rect.adjusted(0, 0, -1, -1))


class ArchiveHealthPanel(QFrame):
    """Sidebar HUD: audio / artwork / transcript / videos coverage."""

    ROWS = (
        ("AUDIO", "missing_audio"),
        ("ARTWORK", "missing_artwork"),
        ("TRANSCRIPT", "missing_transcript"),
        ("VIDEOS", "missing_associated_videos"),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("archiveHealthPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(4)

        title = QLabel("ARCHIVE HEALTH")
        title.setObjectName("archiveHealthTitle")
        layout.addWidget(title)
        layout.addSpacing(2)

        self._bars: dict[str, HealthBar] = {}
        self._values: dict[str, QLabel] = {}
        for label_text, missing_key in self.ROWS:
            bar = HealthBar(self)
            self._bars[missing_key] = bar
            layout.addWidget(bar)

            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 4)
            row.setSpacing(0)
            label = QLabel(label_text)
            label.setObjectName("archiveHealthLabel")
            value = QLabel("—")
            value.setObjectName("archiveHealthValue")
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._values[missing_key] = value
            row.addWidget(label, 1)
            row.addWidget(value)
            layout.addLayout(row)

    def update_from_counts(self, counts: dict[str, int]) -> None:
        total = int(counts.get("total", 0) or 0)
        for _, key in self.ROWS:
            missing = int(counts.get(key, 0) or 0)
            if total <= 0:
                self._bars[key].set_percent(0.0)
                self._values[key].setText("—")
                continue
            present = max(0, total - missing)
            pct = present / total
            self._bars[key].set_percent(pct)
            self._values[key].setText(f"{int(round(pct * 100))}%")


class GradientCheck(QWidget):
    """A holo-gradient disc with a checkmark — the 'pairing confirmed' mark."""

    def __init__(self, parent: QWidget | None = None, diameter: int = 28) -> None:
        super().__init__(parent)
        self._d = diameter
        self.setFixedSize(diameter, diameter)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        grad = QLinearGradient(r.topLeft(), r.bottomRight())
        grad.setColorAt(0.0, QColor("#66ecff"))
        grad.setColorAt(0.5, QColor("#b793ff"))
        grad.setColorAt(1.0, QColor("#ff6ad5"))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(r)
        pen = QPen(QColor("#0a0518"))
        pen.setWidth(max(2, self._d // 9))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        x, y, w = r.left(), r.top(), r.width()
        p.drawLine(int(x + w * 0.28), int(y + w * 0.53), int(x + w * 0.44), int(y + w * 0.68))
        p.drawLine(int(x + w * 0.44), int(y + w * 0.68), int(x + w * 0.74), int(y + w * 0.34))


class PairingBadge(QFrame):
    """Lower-left pairing status: 📱 + a gradient checkmark = visually confirmed."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pairingCard")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(10)
        self._phone = QLabel("📱")
        self._phone.setStyleSheet("font-size:22px;background:transparent;")
        col = QVBoxLayout()
        col.setSpacing(1)
        self._title = QLabel("iPhone paired")
        self._title.setWordWrap(True)
        self._title.setStyleSheet("font-weight:700;color:#fbedff;background:transparent;")
        self._sub = QLabel("ready to catch shares")
        self._sub.setWordWrap(True)
        self._sub.setStyleSheet("font-size:11px;color:#c5b3e6;background:transparent;")
        col.addWidget(self._title)
        col.addWidget(self._sub)
        self._check = GradientCheck(self, 26)
        self._check.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        lay.addWidget(self._phone, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(col, 1)
        lay.addWidget(self._check, 0, Qt.AlignmentFlag.AlignTop)

    def set_state(self, *, paired: bool, detail: str = "") -> None:
        self._check.setVisible(paired)
        if paired:
            self._title.setText("iPhone paired")
            self._sub.setText(detail or "ready to catch shares")
            self._phone.setText("📱")
        else:
            self._title.setText("iPhone not paired")
            self._sub.setText("open Settings to pair")
            self._phone.setText("📱")


class TikTokStatusBadge(QFrame):
    """Lower-left TikTok connection status: 🎵 + a gradient checkmark when the
    saved session is active. Click to open the Connect TikTok dialog."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pairingCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(10)
        self._icon = QLabel("🎵")
        self._icon.setStyleSheet("font-size:22px;background:transparent;")
        col = QVBoxLayout()
        col.setSpacing(1)
        self._title = QLabel("TikTok connected")
        self._title.setWordWrap(True)
        self._title.setStyleSheet("font-weight:700;color:#fbedff;background:transparent;")
        self._sub = QLabel("ready to grab sounds")
        self._sub.setWordWrap(True)
        self._sub.setStyleSheet("font-size:11px;color:#c5b3e6;background:transparent;")
        col.addWidget(self._title)
        col.addWidget(self._sub)
        self._check = GradientCheck(self, 26)
        self._check.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        lay.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(col, 1)
        lay.addWidget(self._check, 0, Qt.AlignmentFlag.AlignTop)

    def set_status(self, status: "tiktok_auth.TikTokAuthStatus") -> None:
        self._check.setVisible(status.connected)
        if status.connected:
            self._title.setText("TikTok connected")
            if status.days_left is not None and status.expiring_soon:
                self._sub.setText(f"session expires in {status.days_left}d — tap to renew")
            elif status.days_left is not None:
                self._sub.setText(f"session active · {status.days_left}d left")
            else:
                self._sub.setText("ready to grab sounds")
            self._icon.setText("🎵")
        else:
            self._title.setText("TikTok not connected")
            self._sub.setText("tap to connect → grab sounds")
            self._icon.setText("🎵")

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class _TikTokLoginWorker(QThread):
    """Runs the interactive (headed) TikTok login off the UI thread; the user
    signs in on TikTok's own page in the window it opens."""

    loginFinished = Signal(bool, str)  # (connected, message)

    def run(self) -> None:
        ensure_media_tools_on_path()  # a Finder-launched GUI has a stripped PATH → node must resolve
        cmd = tiktok_auth.login_command()
        try:
            result = subprocess.run(
                cmd, cwd=str(tiktok_auth.project_cwd()), text=True, capture_output=True,
                timeout=360, check=False,
            )
        except FileNotFoundError:
            self.loginFinished.emit(False, "Could not find Node.js — install it (brew install node) to connect TikTok.")
            return
        except subprocess.TimeoutExpired:
            self.loginFinished.emit(tiktok_auth.connection_status().connected, "Login timed out — try again.")
            return
        except Exception as exc:  # noqa: BLE001 - surface to UI
            self.loginFinished.emit(False, f"{type(exc).__name__}: {exc}")
            return
        status = tiktok_auth.connection_status()
        if status.connected:
            tiktok_auth.harden_state_file()  # ensure the saved session is 0600
            self.loginFinished.emit(True, "")
            return
        stderr = result.stderr or ""
        if "Cannot find module" in stderr and "playwright" in stderr.lower():
            self.loginFinished.emit(
                False, "Playwright isn't installed. In the Sound Cache folder run: npm install"
            )
        elif "Executable doesn't exist" in stderr or "playwright install" in stderr:
            self.loginFinished.emit(
                False, "The browser isn't installed. Run: npx playwright install chromium"
            )
        elif result.returncode == 6:
            self.loginFinished.emit(False, "The login window was closed before sign-in finished.")
        elif result.returncode == 7:
            self.loginFinished.emit(False, "Timed out waiting for sign-in.")
        else:
            tail = stderr.strip().splitlines()[-1:] or [""]
            self.loginFinished.emit(False, tail[0] or "Login did not complete.")


class TikTokConnectDialog(QDialog):
    """First-run onboarding: explain *why* a logged-in TikTok session is needed,
    then connect one via a real login window (or import an existing session)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connect TikTok")
        self.setMinimumWidth(520)
        self._worker: _TikTokLoginWorker | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        lead = QLabel("Connect your TikTok account")
        lead.setObjectName("previewTitle")
        layout.addWidget(lead)

        why = QLabel(
            "Sound Cache saves the <b>real audio</b> of a TikTok sound — the actual track, "
            "not a screen recording.<br><br>"
            "• TikTok only hands that audio to a <b>logged-in browser</b>, so Sound Cache "
            "borrows your TikTok session to grab the sounds you save.<br>"
            "• You'll sign in on <b>TikTok's own page</b> — Sound Cache never sees your "
            "password. Only the session is saved, and only on this Mac.<br>"
            "• Disconnect anytime. Sessions expire after a while; just reconnect when they do."
        )
        why.setWordWrap(True)
        why.setObjectName("onboardBody")
        layout.addWidget(why)

        self.status_badge = TikTokStatusBadge()
        layout.addWidget(self.status_badge)

        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setObjectName("muted")
        layout.addWidget(self.detail)

        actions = QHBoxLayout()
        self.import_btn = QPushButton("Import login file…")
        self.import_btn.setToolTip("Advanced: use an existing Playwright storageState.json")
        self.import_btn.clicked.connect(self.import_state)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self.disconnect_state)
        self.connect_btn = QPushButton("Connect TikTok account →")
        self.connect_btn.setObjectName("primaryButton")
        self.connect_btn.clicked.connect(self.start_login)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        actions.addWidget(self.import_btn)
        actions.addWidget(self.disconnect_btn)
        actions.addStretch(1)
        actions.addWidget(self.connect_btn)
        actions.addWidget(close)
        layout.addLayout(actions)

        privacy = QLabel(
            f"Saved at {tiktok_auth.state_path()} (file-private). "
            "Used only to fetch the sounds you save — never shared."
        )
        privacy.setWordWrap(True)
        privacy.setObjectName("muted")
        layout.addWidget(privacy)

        self._refresh()

    def _refresh(self) -> None:
        status = tiktok_auth.connection_status()
        self.status_badge.set_status(status)
        self.detail.setText(status.reason)
        self.disconnect_btn.setEnabled(status.connected)
        self.connect_btn.setText("Reconnect TikTok →" if status.connected else "Connect TikTok account →")

    def start_login(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self.connect_btn.setEnabled(False)
        self.import_btn.setEnabled(False)
        self.detail.setText(
            "Opening a TikTok login window… sign in there, then come back. "
            "This window updates automatically."
        )
        self._worker = _TikTokLoginWorker(self)
        self._worker.loginFinished.connect(self._on_login_finished)
        self._worker.start()

    def _on_login_finished(self, connected: bool, message: str) -> None:
        self._worker = None
        self.connect_btn.setEnabled(True)
        self.import_btn.setEnabled(True)
        self._refresh()
        if connected:
            self.detail.setText("✓ TikTok connected. You can close this and import your sounds.")
        elif message:
            self.detail.setText(message)

    def import_state(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import TikTok login (storageState.json)", "", "Login state (*.json)"
        )
        if not path:
            return
        src = Path(path)
        if not tiktok_auth.is_valid_state_file(src):
            self.detail.setText("That file isn't a valid logged-in TikTok session.")
            return
        dst = tiktok_auth.state_path()
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            os.chmod(dst, 0o600)
        except OSError as exc:
            self.detail.setText(f"Could not import: {exc}")
            return
        self._refresh()
        self.detail.setText("✓ Imported a TikTok session.")

    def disconnect_state(self) -> None:
        tiktok_auth.disconnect()
        self._refresh()
        self.detail.setText("Disconnected. Reconnect whenever you want to grab more sounds.")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.wait(2000)
        super().closeEvent(event)


class PlayButtonDelegate(QStyledItemDelegate):
    def __init__(self, play_callback, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.play_callback = play_callback
        # music_id of the row currently playing, so its button shows ▮▮.
        self.playing_music_id: str | None = None

    def paint(self, painter, option, index) -> None:  # noqa: D102, N802 - Qt override
        button = QStyleOptionButton()
        button.rect = option.rect.adjusted(5, 2, -5, -2)
        if not index.data(PLAYABLE_ROLE):
            button.text = "—"
        elif self.playing_music_id is not None and str(index.data(Qt.ItemDataRole.UserRole)) == self.playing_music_id:
            button.text = "▮▮"
        else:
            button.text = "▶"
        button.state = QStyle.StateFlag.State_Raised
        if index.data(PLAYABLE_ROLE):
            button.state |= QStyle.StateFlag.State_Enabled
        if option.state & QStyle.StateFlag.State_MouseOver:
            button.state |= QStyle.StateFlag.State_MouseOver
        style = option.widget.style() if option.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_PushButton, button, painter, option.widget)

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: D102, N802 - Qt override
        if event.type() != QEvent.Type.MouseButtonRelease or not index.data(PLAYABLE_ROLE):
            return False
        if hasattr(event, "button") and event.button() != Qt.MouseButton.LeftButton:
            return False
        music_id = index.data(Qt.ItemDataRole.UserRole)
        if music_id:
            self.play_callback(str(music_id))
            return True
        return False


class FavoriteButtonDelegate(QStyledItemDelegate):
    def __init__(self, toggle_callback, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.toggle_callback = toggle_callback

    def paint(self, painter, option, index) -> None:  # noqa: D102, N802 - Qt override
        button = QStyleOptionButton()
        button.rect = option.rect.adjusted(5, 2, -5, -2)
        button.text = "★" if index.data(FAVORITE_ROLE) else "☆"
        button.state = QStyle.StateFlag.State_Raised | QStyle.StateFlag.State_Enabled
        if option.state & QStyle.StateFlag.State_MouseOver:
            button.state |= QStyle.StateFlag.State_MouseOver
        style = option.widget.style() if option.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_PushButton, button, painter, option.widget)

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: D102, N802 - Qt override
        if event.type() != QEvent.Type.MouseButtonRelease:
            return False
        if hasattr(event, "button") and event.button() != Qt.MouseButton.LeftButton:
            return False
        music_id = index.data(Qt.ItemDataRole.UserRole)
        if music_id:
            self.toggle_callback(str(music_id))
            return True
        return False


class SettingsDialog(QDialog):
    def __init__(self, *, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Shortcut Relay")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.relay_url = QLineEdit(settings.relay_base_url())
        self.relay_url.setPlaceholderText("https://api.soundcache.io")
        self.pair_code = QLineEdit(settings.relay_pair_code())
        self.pair_code.setPlaceholderText("VAULT-1234")
        self.device_id = QLineEdit(settings.relay_device_id())
        self.device_secret = QLineEdit(settings.relay_device_secret())
        self.device_secret.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Relay URL", self.relay_url)
        form.addRow("Pair code", self.pair_code)
        form.addRow("Device ID", self.device_id)
        form.addRow("Device secret", self.device_secret)
        layout.addLayout(form)

        self.telemetry_checkbox = QCheckBox(
            "Share anonymized save events to the public leaderboard (sound id, platform, title — no account)"
        )
        self.telemetry_checkbox.setChecked(settings.telemetry_enabled())
        layout.addWidget(self.telemetry_checkbox)

        self.result_label = QLabel("Create pairing code on the relay, then add it to the iOS Shortcut.")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

        actions = QHBoxLayout()
        create_pairing = QPushButton("Create pairing code")
        create_pairing.clicked.connect(self.create_pairing_code)
        pair_phone = QPushButton("Pair iPhone…")
        pair_phone.setToolTip("Scan-to-set-up QR + export the Save to Sound Cache shortcut")
        pair_phone.clicked.connect(self.open_pair_phone)
        connect_tiktok = QPushButton("Connect TikTok…")
        connect_tiktok.setToolTip("Sign in so Sound Cache can grab the real audio of saved sounds")
        connect_tiktok.clicked.connect(lambda: TikTokConnectDialog(parent=self).exec())
        save = QPushButton("Save relay settings")
        save.setObjectName("primaryButton")
        save.clicked.connect(self.save_settings)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        actions.addWidget(create_pairing)
        actions.addWidget(pair_phone)
        actions.addWidget(connect_tiktok)
        actions.addStretch(1)
        actions.addWidget(save)
        actions.addWidget(close)
        layout.addLayout(actions)

    def save_settings(self) -> None:
        self.settings.set_relay_config(
            base_url=self.relay_url.text(),
            pair_code=self.pair_code.text(),
            device_id=self.device_id.text(),
            device_secret=self.device_secret.text(),
        )
        self.settings.set_telemetry_enabled(self.telemetry_checkbox.isChecked())
        self.result_label.setText("Relay settings saved. Device secret is stored locally and hidden here.")

    def create_pairing_code(self) -> None:
        base_url = self.relay_url.text().strip().rstrip("/")
        if not base_url:
            self.result_label.setText("Enter a relay URL first.")
            return
        try:
            request = urllib.request.Request(
                f"{base_url}/v1/pairing/create",
                data=json.dumps({"device_name": "Sound Cache Desktop"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=10, context=ssl_context()) as response:  # nosec B310 - user relay URL
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            self.result_label.setText(f"Pairing failed: {exc}")
            return
        self.pair_code.setText(str(payload.get("pair_code") or ""))
        self.device_id.setText(str(payload.get("device_id") or ""))
        self.device_secret.setText(str(payload.get("device_secret") or ""))
        self.save_settings()
        self.result_label.setText(
            "Create pairing code succeeded. Put this pair code in the iOS Shortcut: "
            f"{self.pair_code.text()}"
        )

    def open_pair_phone(self) -> None:
        # Persist whatever is in the fields so the QR/export use current values.
        self.save_settings()
        if not self.relay_url.text().strip() or not self.pair_code.text().strip():
            self.result_label.setText(
                "Set a relay URL and pair code first (use “Create pairing code”), then pair your iPhone."
            )
            return
        PairPhoneDialog(
            relay_url=self.relay_url.text().strip().rstrip("/"),
            pair_code=self.pair_code.text().strip().upper(),
            parent=self,
        ).exec()


def _qr_pixmap(data: str, target_px: int = 320, *, quiet_zone: int = 4) -> QPixmap | None:
    """Render ``data`` to a crisp black-on-white QR QPixmap, or None if qrcode is missing."""
    try:
        matrix = shortcut_builder.qr_matrix(data)
    except Exception:
        return None
    modules = len(matrix) + quiet_zone * 2
    scale = max(1, target_px // modules)
    dim = modules * scale
    pixmap = QPixmap(dim, dim)
    pixmap.fill(QColor("#ffffff"))
    painter = QPainter(pixmap)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#0a0518"))
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                painter.drawRect((x + quiet_zone) * scale, (y + quiet_zone) * scale, scale, scale)
    painter.end()
    return pixmap


class PairPhoneDialog(QDialog):
    """Scan-to-set-up QR + one-click export of the Save to Sound Cache shortcut."""

    def __init__(self, *, relay_url: str, pair_code: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.relay_url = relay_url.strip().rstrip("/")
        self.pair_code = pair_code.strip().upper()
        self.setWindowTitle("Pair iPhone")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        intro = QLabel(
            "Get the <b>Save to Sound Cache</b> shortcut onto your iPhone. It adds a "
            "share-sheet button to TikTok, Instagram, YouTube, X — share a link and it "
            "lands in your vault."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # --- QR ---
        qr_box = QHBoxLayout()
        self.qr_label = QLabel()
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setMinimumSize(300, 300)
        setup = shortcut_builder.setup_url(self.relay_url, self.pair_code)
        pixmap = _qr_pixmap(setup, 300)
        if pixmap is not None:
            self.qr_label.setPixmap(pixmap)
        else:
            self.qr_label.setWordWrap(True)
            self.qr_label.setText(
                "QR needs the qrcode package (pip install qrcode).\n\nScan-to-set-up URL:\n" + setup
            )
        qr_box.addStretch(1)
        qr_box.addWidget(self.qr_label)
        qr_box.addStretch(1)
        layout.addLayout(qr_box)

        scan_hint = QLabel(
            "1. Scan this with your iPhone camera → opens the guided setup page.<br>"
            "2. Tap <b>Add Shortcut</b>, then paste the two values below if asked."
        )
        scan_hint.setWordWrap(True)
        layout.addWidget(scan_hint)

        # --- copyable values ---
        values = QFormLayout()
        self.relay_field = QLineEdit(self.relay_url)
        self.relay_field.setReadOnly(True)
        self.code_field = QLineEdit(self.pair_code)
        self.code_field.setReadOnly(True)
        relay_row = QHBoxLayout()
        relay_row.addWidget(self.relay_field, 1)
        relay_copy = QPushButton("Copy")
        relay_copy.clicked.connect(lambda: self._copy(self.relay_url, "Relay URL copied"))
        relay_row.addWidget(relay_copy)
        code_row = QHBoxLayout()
        code_row.addWidget(self.code_field, 1)
        code_copy = QPushButton("Copy")
        code_copy.clicked.connect(lambda: self._copy(self.pair_code, "Pair code copied"))
        code_row.addWidget(code_copy)
        values.addRow("Relay URL", relay_row)
        values.addRow("Pair code", code_row)
        layout.addLayout(values)

        self.status_label = QLabel(
            "Prefer building it by hand? Export a pre-filled shortcut file, or follow the "
            "recipe in docs/ios-shortcut-v1-recipe.md."
        )
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        actions = QHBoxLayout()
        save_qr = QPushButton("Save QR image…")
        save_qr.clicked.connect(self.save_qr)
        export = QPushButton("Export shortcut file…")
        export.clicked.connect(self.export_shortcut)
        close = QPushButton("Close")
        close.setObjectName("primaryButton")
        close.clicked.connect(self.accept)
        actions.addWidget(save_qr)
        actions.addWidget(export)
        actions.addStretch(1)
        actions.addWidget(close)
        layout.addLayout(actions)

    def _copy(self, text: str, message: str) -> None:
        QApplication.clipboard().setText(text)
        self.status_label.setText(message)

    def save_qr(self) -> None:
        setup = shortcut_builder.setup_url(self.relay_url, self.pair_code)
        path, _ = QFileDialog.getSaveFileName(self, "Save setup QR", "sound-cache-pairing.png", "PNG image (*.png)")
        if not path:
            return
        pixmap = _qr_pixmap(setup, 720)
        if pixmap is None:
            # qrcode missing: fall back to the scalable SVG next to the chosen path.
            try:
                svg_path = path[:-4] + ".svg" if path.lower().endswith(".png") else path + ".svg"
                with open(svg_path, "w", encoding="utf-8") as handle:
                    handle.write(shortcut_builder.qr_svg(setup))
                self.status_label.setText(f"Saved QR (SVG): {svg_path}")
            except Exception as exc:
                self.status_label.setText(f"Could not save QR: {exc}")
            return
        if pixmap.save(path, "PNG"):
            self.status_label.setText(f"Saved QR: {path}")
        else:
            self.status_label.setText("Could not save the QR image.")

    def export_shortcut(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Save to Sound Cache shortcut",
            "Save to Sound Cache.unsigned.plist",
            "Shortcut plist (*.plist)",
        )
        if not path:
            return
        try:
            data = shortcut_builder.workflow_plist_bytes(self.relay_url, self.pair_code)
            with open(path, "wb") as handle:
                handle.write(data)
        except Exception as exc:
            self.status_label.setText(f"Export failed: {exc}")
            return
        self.status_label.setText(
            f"Exported {path}. iOS won’t import a raw plist directly — use the QR/iCloud "
            "route, or import via community tooling (routinehub / shortcuts-toolkit)."
        )


class _ImportWorker(QThread):
    """Runs relay-poll + inbox download/ingest off the UI thread (downloads are slow)."""

    importFinished = Signal(object, object)  # (outcomes, error|None)
    itemIngested = Signal(str, str, str)  # music_id, folder, audio — fired as each lands

    def __init__(self, vm, *, base_url, pair_code, device_id, device_secret, reporter=None, parent=None) -> None:
        super().__init__(parent)
        self._vm = vm
        self._creds = (base_url, pair_code, device_id, device_secret)
        self._reporter = reporter

    def _emit_item(self, outcome) -> None:
        # Pipeline hook: as soon as a sound finishes downloading, hand its id + paths
        # to the UI so it can queue transcription immediately (ASR runs concurrently
        # with the throttled download loop) instead of waiting for the whole batch.
        if (
            getattr(outcome, "status", "") == "ingested"
            and getattr(outcome, "music_id", "")
            and getattr(outcome, "audio_path", None)
        ):
            self.itemIngested.emit(
                str(outcome.music_id), str(outcome.folder or ""), str(outcome.audio_path or "")
            )

    def run(self) -> None:
        try:
            base_url, pair_code, device_id, device_secret = self._creds
            if all((base_url, pair_code, device_id, device_secret)):
                self._vm.poll_relay_inbox(
                    base_url=base_url,
                    pair_code=pair_code,
                    device_id=device_id,
                    device_secret=device_secret,
                )
            outcomes = self._vm.import_pending(
                reporter=self._reporter, should_stop=self.isInterruptionRequested, on_item=self._emit_item
            )
            self.importFinished.emit(outcomes, None)
        except Exception as exc:  # noqa: BLE001 - surface failure to the UI thread
            self.importFinished.emit([], f"{type(exc).__name__}: {exc}")


class _TranscriptionWorker(QThread):
    """Transcribe a bounded batch of just-imported sounds off the UI thread.

    Decoupled from the import itself so transcripts fill in hands-free even if the
    inline attempt was skipped, without blocking the import or freezing the UI."""

    progress = Signal(int, int)  # done, total
    finished_ok = Signal(int, object)  # transcribed_count, error|None

    def __init__(self, vm, targets, parent=None) -> None:
        super().__init__(parent)
        self._vm = vm
        self._targets = targets

    def run(self) -> None:
        try:
            count = self._vm.transcribe_targets(
                self._targets,
                progress=lambda done, total, _mid: self.progress.emit(done, total),
                should_stop=self.isInterruptionRequested,
            )
            self.finished_ok.emit(count, None)
        except Exception as exc:  # noqa: BLE001 - surface to UI thread
            self.finished_ok.emit(0, exc)


class _TranscriptionQueueWorker(QThread):
    """Persistent ASR consumer: transcribes each sound the moment it finishes
    downloading, so transcription runs concurrently with the (deliberately throttled,
    slower) download loop instead of waiting for the whole batch. The download thread
    is I/O + subprocess bound; this one is CPU bound — they don't contend.

    Stays alive across imports and idles (cheap 0.4s poll) when its queue is empty.
    Cooperative-cancel: while idle the get() timeout keeps the loop checking
    isInterruptionRequested; while transcribing, should_stop is forwarded into the
    whisper segment loop so the bulk of a long transcription is cancellable between
    segments (the one uninterruptible window is the initial VAD preprocessing of the
    current item). Net effect: a quit returns well within closeEvent's wait budget —
    no QThread left running at teardown."""

    transcribed = Signal(str)  # music_id that gained a transcript
    progress = Signal(int, int)  # done, enqueued

    def __init__(self, vm, parent=None) -> None:
        super().__init__(parent)
        self._vm = vm
        self._queue: "queue.Queue[tuple[str, str, str] | None]" = queue.Queue()
        # _enqueued is bumped on the UI thread (enqueue), _done on this worker thread;
        # a tiny lock keeps the (done, enqueued) pair a consistent snapshot for the
        # progress label.
        self._counter_lock = threading.Lock()
        self._enqueued = 0
        self._done = 0

    def enqueue(self, music_id: str, folder: str, audio: str) -> None:
        with self._counter_lock:
            self._enqueued += 1
        self._queue.put((str(music_id), str(folder), str(audio)))

    def run(self) -> None:
        transcriber = None
        built = False
        try:
            while not self.isInterruptionRequested():
                try:
                    item = self._queue.get(timeout=0.4)
                except queue.Empty:
                    continue
                if item is None or self.isInterruptionRequested():
                    break
                music_id, folder, audio = item
                if not built:
                    from sound_vault.ingest.factory import build_transcriber

                    transcriber = build_transcriber()  # built once; None if ASR unavailable
                    built = True
                if transcriber is not None:
                    try:
                        ok = self._vm.transcribe_one(
                            music_id, folder, audio,
                            transcriber=transcriber, should_stop=self.isInterruptionRequested,
                        )
                    except Exception as exc:  # noqa: BLE001 - per-item best-effort, never fatal
                        write_event("gui.queue_transcribe_error", music_id=str(music_id), **exception_fields(exc))
                        ok = False
                    if ok:
                        self.transcribed.emit(music_id)
                with self._counter_lock:
                    done, enqueued = self._done + 1, self._enqueued
                    self._done = done
                self.progress.emit(done, enqueued)
        except Exception as exc:  # noqa: BLE001 - a dying run() must surface, not vanish
            write_event("gui.queue_transcribe_worker_fatal", **exception_fields(exc))


class _PollWorker(QThread):
    """Lightweight off-thread relay poll (pull links into the inbox, no download)."""

    pollFinished = Signal(object, object)  # (items, error|None)

    def __init__(self, vm, *, base_url, pair_code, device_id, device_secret, parent=None) -> None:
        super().__init__(parent)
        self._vm = vm
        self._creds = (base_url, pair_code, device_id, device_secret)

    def run(self) -> None:
        try:
            base_url, pair_code, device_id, device_secret = self._creds
            items = self._vm.poll_relay_inbox(
                base_url=base_url,
                pair_code=pair_code,
                device_id=device_id,
                device_secret=device_secret,
            )
            self.pollFinished.emit(items, None)
        except Exception as exc:  # noqa: BLE001 - surface failure to the UI thread
            self.pollFinished.emit(None, exc)


class OnboardingDialog(QDialog):
    """First-run setup wizard: welcome → choose a vault location → connect TikTok
    (optional) → done. The chosen vault is read back via chosen_vault()."""

    def __init__(self, *, default_vault: Path, connect_tiktok, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set up Sound Cache")
        self.setMinimumSize(580, 420)
        self._connect_tiktok = connect_tiktok
        self._chosen_vault = Path(default_vault)

        outer = QVBoxLayout(self)
        outer.setSpacing(14)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.stack.addWidget(self._welcome_page())
        self.stack.addWidget(self._vault_page(default_vault))
        self.stack.addWidget(self._tiktok_page())
        self.stack.addWidget(self._done_page())

        row = QHBoxLayout()
        self.back_btn = QPushButton("Back")
        self.back_btn.clicked.connect(self._go_back)
        self.skip_btn = QPushButton("Skip setup")
        self.skip_btn.clicked.connect(self.reject)
        self.next_btn = QPushButton("Next →")
        self.next_btn.setObjectName("primaryButton")
        self.next_btn.clicked.connect(self._go_next)
        row.addWidget(self.back_btn)
        row.addWidget(self.skip_btn)
        row.addStretch(1)
        row.addWidget(self.next_btn)
        outer.addLayout(row)

        self.stack.currentChanged.connect(self._sync_buttons)
        self._sync_buttons()

    def _title(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("previewTitle")
        return lbl

    def _body(self, html: str) -> QLabel:
        lbl = QLabel(html)
        lbl.setWordWrap(True)
        lbl.setObjectName("onboardBody")
        return lbl

    def _welcome_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(10)
        lay.addWidget(self._title("Welcome to Sound Cache 🎧"))
        lay.addWidget(self._body(
            "Sound Cache is a <b>local-first vault</b> for the sounds you save — the real "
            "audio, organized in a folder you own.<br><br>"
            "No login. No cloud. Just files on your machine.<br><br>"
            "This quick setup picks where your vault lives and (optionally) connects TikTok "
            "so Sound Cache can fetch real audio."))
        lay.addStretch(1)
        return page

    def _vault_page(self, default_vault: Path) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(10)
        lay.addWidget(self._title("Where should your vault live?"))
        lay.addWidget(self._body(
            "Pick a folder for your sounds. Everything Sound Cache saves goes here — "
            "back it up, sync it, or move it later; it's just files.<br><br>"
            "Tip: an external or network drive works great for a large library."))
        row_w = QWidget()
        row = QHBoxLayout(row_w)
        row.setContentsMargins(0, 0, 0, 0)
        self.vault_edit = QLineEdit(str(default_vault))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_vault)
        row.addWidget(self.vault_edit, 1)
        row.addWidget(browse)
        lay.addWidget(row_w)
        self.vault_hint = QLabel("")
        self.vault_hint.setObjectName("muted")
        self.vault_hint.setWordWrap(True)
        lay.addWidget(self.vault_hint)
        lay.addStretch(1)
        self.vault_edit.textChanged.connect(self._update_vault_hint)
        self._update_vault_hint()
        return page

    def _tiktok_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(10)
        lay.addWidget(self._title("Connect TikTok (optional)"))
        lay.addWidget(self._body(
            "To save the <b>real audio</b> of a TikTok sound, Sound Cache borrows your "
            "logged-in TikTok session — you sign in on TikTok's own page, and only the "
            "session is stored, only on this Mac.<br><br>"
            "You can skip this and do it later from <b>Vault → Connect TikTok</b>. Sound packs "
            "and pasted links still queue without it — they just need a session to fetch audio."))
        connect_btn = QPushButton("Connect TikTok now…")
        connect_btn.setObjectName("primaryButton")
        connect_btn.clicked.connect(lambda: self._connect_tiktok())
        lay.addWidget(connect_btn, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addStretch(1)
        return page

    def _done_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(10)
        lay.addWidget(self._title("You're all set ✦"))
        lay.addWidget(self._body(
            "Add sounds any way you like:<br><br>"
            "• <b>Paste a link</b> at soundcache.io/get, or share from the iOS shortcut.<br>"
            "• <b>Import a sound pack</b> — File → Import Sound Pack…<br>"
            "• <b>Import a TikTok data export</b> — File → Import TikTok Export…<br><br>"
            "Your library lives in the folder you chose. Hit Finish to open it."))
        lay.addStretch(1)
        return page

    def _browse_vault(self) -> None:
        start = self.vault_edit.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Choose a folder for your vault", start)
        if selected:
            self.vault_edit.setText(selected)

    def _update_vault_hint(self) -> None:
        text = self.vault_edit.text().strip()
        if not text:
            self.vault_hint.setText("Choose a folder for your sounds.")
            return
        path = Path(text).expanduser()
        try:
            has_vault = (path / "catalog").exists() or (path.exists() and any(path.glob("sounds/*")))
        except OSError:
            has_vault = False
        if has_vault:
            self.vault_hint.setText("Opens the existing vault in this folder.")
        elif path.exists():
            self.vault_hint.setText("Creates a new vault in this folder.")
        else:
            self.vault_hint.setText("This folder will be created.")

    def _sync_buttons(self) -> None:
        idx = self.stack.currentIndex()
        self.back_btn.setEnabled(idx > 0)
        self.next_btn.setText("Finish" if idx == self.stack.count() - 1 else "Next →")

    def _go_back(self) -> None:
        self.stack.setCurrentIndex(max(0, self.stack.currentIndex() - 1))

    def _go_next(self) -> None:
        idx = self.stack.currentIndex()
        if idx == 1:  # leaving the vault page — validate + record the choice
            text = self.vault_edit.text().strip()
            if not text:
                QMessageBox.warning(self, "Pick a folder", "Choose where your vault should live.")
                return
            self._chosen_vault = Path(text).expanduser()
        if idx == self.stack.count() - 1:
            self.accept()
            return
        self.stack.setCurrentIndex(idx + 1)

    def chosen_vault(self) -> Path:
        return self._chosen_vault


class SoundVaultWindow(QMainWindow):
    previewHydrated = Signal(int, str, object)

    def __init__(self, *, vault_root: Path | None = None, settings: AppSettings | None = None) -> None:
        super().__init__()
        self.settings = settings or AppSettings()
        write_event("gui.window_init_start")
        self.setWindowTitle("Sound Cache — local sound archive")
        self.resize(1420, 860)
        # The layout now degrades gracefully (sidebar scrolls, inspector stacks to
        # artwork width, transport capsule + badges shrink/wrap), so the window can
        # go smaller without scrunching unreadably.
        self.setMinimumSize(1080, 700)
        self.vault_root = resolve_vault_root(vault_root or self.settings.vault_root())
        write_event("gui.vault_resolved", vault_root=str(self.vault_root))
        self.vm = LibraryViewModel(
            vault_root=self.vault_root,
            index_path=index_path_for_vault(self.vault_root),
            load_sidecars=False,
            sidecar_mode="summary",
        )
        self.current_rows = []
        self.records_by_id = {}
        self.current_inbox_rows = []
        self.inbox_rows_by_id = {}
        self.current_dedupe_groups = []
        self.dedupe_groups_by_id = {}
        self.current_preview_record = None
        self._pending_selected_music_id: str | None = None
        self.active_library_filter = "all"
        self.library_sort_column = POPULARITY_COL
        self.library_sort_order = Qt.SortOrder.DescendingOrder
        self.continuous_play_enabled = False
        self._index_future = None
        self._index_timer = QTimer(self)
        self._index_timer.setInterval(150)
        self._index_timer.timeout.connect(self._finish_async_index)
        self._worker_future = None
        self._worker_job_name = ""
        self._worker_timer = QTimer(self)
        self._worker_timer.setInterval(250)
        self._worker_timer.timeout.connect(self._finish_async_worker_job)
        # Concurrent transcription pipeline + global import progress bar.
        self._transcribe_queue_worker = None
        self._library_needs_transcript_refresh = False
        self._transcript_refresh_timer = QTimer(self)
        self._transcript_refresh_timer.setInterval(2500)
        self._transcript_refresh_timer.setSingleShot(True)
        self._transcript_refresh_timer.timeout.connect(self._flush_transcript_refresh)
        self._import_progress_timer = QTimer(self)
        self._import_progress_timer.setInterval(1000)
        self._import_progress_timer.timeout.connect(self._tick_import_progress)
        self._import_rate_samples: "deque[tuple[float, int]]" = deque(maxlen=40)
        self._search_timer = QTimer(self)
        self._search_timer.setInterval(80)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self.refresh_table)
        # Background relay poll so shared links land in the inbox without a click.
        self._poll_worker = None
        self._poll_in_flight = False
        self._relay_poll_timer = QTimer(self)
        self._relay_poll_timer.setInterval(60_000)
        self._relay_poll_timer.timeout.connect(self._auto_poll_relay)
        self.audio_output = None
        self.audio_player = None
        self.external_audio_process: subprocess.Popen | None = None
        self.external_audio_target: Path | None = None
        self._external_audio_started_at: float | None = None
        self._external_audio_duration_ms: int = 0
        # Wall-clock scrubber anchor: where in the song the current external
        # process began (non-zero after a seek), and whether the chosen backend
        # can honour a start offset (ffplay yes, afplay no).
        self._external_audio_base_offset_ms: int = 0
        self._external_seek_supported: bool = False
        # Probed audio durations keyed by file path — so the scrubber has a real
        # range (and a correct "/ total" time) even when the sound's metadata never
        # captured duration_seconds. Cached so re-playing a sound is instant.
        self._duration_probe_cache: dict[str, int] = {}
        self.playing_music_id: str | None = None
        self._external_audio_timer = QTimer(self)
        self._external_audio_timer.setInterval(300)
        self._external_audio_timer.timeout.connect(self._poll_external_audio)
        # User-notes editor state (debounced autosave; flush on switch/close).
        self._notes_music_id: str | None = None
        self._loading_notes = False
        self._notes_save_timer = QTimer(self)
        self._notes_save_timer.setInterval(700)
        self._notes_save_timer.setSingleShot(True)
        self._notes_save_timer.timeout.connect(self._flush_user_notes)
        from concurrent.futures import ThreadPoolExecutor
        self._preview_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sound-vault-preview")
        self._preview_token = 0
        self.previewHydrated.connect(self._apply_hydrated_preview)
        self._build_ui()
        self._setup_import_progress()
        self._build_menu_bar()
        self.setup_keyboard_shortcuts()
        self.restore_library_search_state()
        self.update_pairing_status()
        self.refresh_portal_tabs()
        write_event("gui.window_ready", vault_root=str(self.vault_root))
        if _env_flag("SOUND_VAULT_SAFE_MODE") or _env_flag("SOUND_VAULT_DISABLE_AUTO_INDEX"):
            self.worker_label.setText("Worker\nauto-index disabled")
            self._set_index_status("INDEX PAUSED", state="waiting")
            write_event("gui.auto_index_disabled", vault_root=str(self.vault_root))
        else:
            self.rebuild_index()
        self._start_relay_auto_poll()
        self._maybe_run_onboarding()
        self._maybe_prompt_tiktok_connect()

    def _maybe_prompt_tiktok_connect(self) -> None:
        """First-run nudge: if the user has paired an iPhone (so they intend to
        save sounds) but hasn't connected TikTok yet, open the onboarding once on
        launch — otherwise their first share would silently fail to fetch audio."""
        if _env_flag("SOUND_VAULT_DISABLE_TIKTOK_PROMPT"):
            return
        if not self.settings.relay_pair_code().strip():
            return  # not set up to receive shares yet — don't nag
        if tiktok_auth.connection_status().connected:
            return
        QTimer.singleShot(1200, self.open_tiktok_connect)

    def _start_relay_auto_poll(self) -> None:
        """Begin background relay polling when a pairing is configured.

        Safe to call repeatedly (e.g. after saving relay settings). Disabled in
        headless test/CI runs to keep no live network calls running.
        """
        if _env_flag("SOUND_VAULT_DISABLE_RELAY_POLL"):
            return
        if not self.settings.relay_pair_code().strip():
            return
        if not self._relay_poll_timer.isActive():
            self._relay_poll_timer.start()
            QTimer.singleShot(1500, self._auto_poll_relay)  # one prompt poll on launch

    def _auto_poll_relay(self) -> None:
        """Silently pull new relay links into the inbox off the UI thread."""
        # Single in-flight flag (not worker.isRunning()) so a timer tick that
        # races the finished-signal can't spawn a second concurrent poll.
        if self._poll_in_flight:
            return
        if getattr(self, "_import_worker", None) is not None and self._import_worker.isRunning():
            return
        base_url = self.settings.relay_base_url()
        pair_code = self.settings.relay_pair_code()
        device_id = self.settings.relay_device_id()
        device_secret = self.settings.relay_device_secret()
        if not all((base_url, pair_code, device_id, device_secret)):
            return
        self._poll_in_flight = True
        self._poll_worker = _PollWorker(
            self.vm,
            base_url=base_url,
            pair_code=pair_code,
            device_id=device_id,
            device_secret=device_secret,
            parent=self,
        )
        self._poll_worker.pollFinished.connect(self._on_auto_poll_finished)
        self._poll_worker.start()

    def _on_auto_poll_finished(self, items, error) -> None:
        self._poll_in_flight = False
        self._poll_worker = None
        if error is not None:
            write_event("gui.auto_poll_failed", **exception_fields(error))
            self.statusBar().showMessage("Relay poll failed — check Settings (see logs).", 5000)
            return
        if items:
            self.refresh_inbox()
            self.refresh_review_queues()
            write_event("gui.auto_poll_pulled", count=str(len(items)))
            self.statusBar().showMessage(
                f"{len(items)} new shared link(s) pulled into the inbox.", 5000
            )

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("appShell")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(200)
        sidebar.setMaximumWidth(320)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 22, 16, 22)
        side.setSpacing(10)
        brand = QWidget()
        brand.setObjectName("brand")
        brand_row = QHBoxLayout(brand)
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(9)
        brand_mark = QLabel()
        brand_mark.setObjectName("brandMark")
        _mark = QPixmap(str(_UI_DIR / "assets" / "app-icon-256.png"))
        if not _mark.isNull():
            brand_mark.setPixmap(
                _mark.scaled(26, 26, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )
        brand_mark.setFixedSize(26, 26)
        brand_text = QLabel("Sound Cache")
        brand_text.setObjectName("brandText")
        brand_row.addWidget(brand_mark)
        brand_row.addWidget(brand_text)
        brand_row.addStretch(1)
        side.addWidget(brand)
        self.nav_buttons = {}
        # The nav + library/playlist list lives in a vertical scroll area so a long
        # list (or a short window) scrolls gracefully instead of scrunching the
        # rows; the health panel + status badges stay pinned below it.
        nav_scroll = QScrollArea()
        nav_scroll.setObjectName("navScroll")
        nav_scroll.setWidgetResizable(True)
        nav_scroll.setFrameShape(QFrame.Shape.NoFrame)
        nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        nav_body = QWidget()
        nav_layout = QVBoxLayout(nav_body)
        nav_layout.setContentsMargins(0, 0, 4, 0)
        nav_layout.setSpacing(10)
        library_row = QHBoxLayout()
        library_row.setSpacing(6)
        self.library_toggle = QPushButton("▾ Library")
        self.library_toggle.setObjectName("navButton")
        self.library_toggle.clicked.connect(self.toggle_library_section)
        self.nav_buttons["library"] = self.library_toggle
        self.add_bin_button = QToolButton()
        self.add_bin_button.setText("+")
        self.add_bin_button.setObjectName("smallAddButton")
        self.add_bin_button.setToolTip("Add sorting bin")
        self.add_bin_button.clicked.connect(self.create_sorting_bin)
        library_row.addWidget(self.library_toggle, 1)
        library_row.addWidget(self.add_bin_button)
        nav_layout.addLayout(library_row)
        self.library_section = QWidget()
        self.library_section_layout = QVBoxLayout(self.library_section)
        self.library_section_layout.setContentsMargins(10, 0, 0, 4)
        self.library_section_layout.setSpacing(5)
        nav_layout.addWidget(self.library_section)
        self.library_bin_buttons = {}
        self.refresh_library_sidebar()
        for label, view_name in [
            ("Ingest inbox", "inbox"),
            ("Review queues", "review"),
            ("Duplicate review", "dedupe"),
            ("Worker status", "worker"),
            ("Settings", "settings"),
        ]:
            btn = QPushButton(label)
            btn.setObjectName("navButton")
            btn.clicked.connect(lambda _checked=False, name=view_name: self.show_view(name))
            self.nav_buttons[view_name] = btn
            nav_layout.addWidget(btn)
        nav_layout.addStretch(1)
        nav_scroll.setWidget(nav_body)
        side.addWidget(nav_scroll, 1)
        self.archive_health_panel = ArchiveHealthPanel()
        side.addWidget(self.archive_health_panel)
        self.pairing_badge = PairingBadge()
        side.addWidget(self.pairing_badge)
        self.update_pairing_status()
        self.tiktok_badge = TikTokStatusBadge()
        self.tiktok_badge.clicked.connect(self.open_tiktok_connect)
        side.addWidget(self.tiktok_badge)
        self.update_tiktok_status()
        shell.addWidget(sidebar)

        main = QWidget()
        main.setObjectName("mainDeck")
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(24, 22, 18, 22)
        main_layout.setSpacing(12)
        top = QHBoxLayout()
        title_box = QVBoxLayout()
        self.title_label = QLabel("Library")
        self.title_label.setObjectName("title")
        vault_str = str(self.vault_root)
        compact = "/".join(self.vault_root.parts[-3:]) if len(self.vault_root.parts) > 3 else vault_str
        self.vault_label = QLabel(compact)
        self.vault_label.setObjectName("muted")
        self.vault_label.setToolTip(vault_str)
        title_box.addWidget(self.title_label)
        title_box.addWidget(self.vault_label)
        top.addLayout(title_box)
        top.addStretch(1)
        choose = QPushButton("Choose vault")
        choose.clicked.connect(self.choose_vault)
        self.rebuild_button = QPushButton("Rebuild index")
        self.rebuild_button.setObjectName("primaryButton")
        self.rebuild_button.clicked.connect(self.rebuild_index)
        top.addWidget(choose)
        top.addWidget(self.rebuild_button)
        main_layout.addLayout(top)
        main_layout.addWidget(self._build_jukebox_chrome())

        self.stack = QStackedWidget()
        self.library_view = self._build_library_view()
        self.inbox_view = self._build_inbox_view()
        self.review_view = self._build_review_view()
        self.dedupe_view = self._build_dedupe_view()
        self.worker_view = self._build_worker_view()
        self.stack.addWidget(self.library_view)
        self.stack.addWidget(self.inbox_view)
        self.stack.addWidget(self.review_view)
        self.stack.addWidget(self.dedupe_view)
        self.stack.addWidget(self.worker_view)
        main_layout.addWidget(self.stack, 1)
        shell.addWidget(main, 1)

        preview = self._build_preview_panel()
        shell.addWidget(preview)
        _assets = Path(__file__).resolve().parent / "assets"
        styled = STYLESHEET.replace(
            "__DECK_BG__", (_assets / "deck-bg.png").as_posix()
        ).replace("__SIDEBAR_BG__", (_assets / "sidebar-bg.png").as_posix())
        self.setStyleSheet(styled)
        self.show_view("library")

    def toggle_library_section(self) -> None:
        self.library_section.setVisible(not self.library_section.isVisible())
        self.library_toggle.setText(("▾" if self.library_section.isVisible() else "▸") + " Library")

    def refresh_library_sidebar(self) -> None:
        if not hasattr(self, "library_section_layout"):
            return
        while self.library_section_layout.count():
            item = self.library_section_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.library_bin_buttons = {}
        sidebar_rows = [
            ("all", "All sounds", "Show full library"),
            ("favorites", "★ Favorites", "Show favorited sounds; drop rows here to favorite"),
            ("smart:needs_transcript", "Not transcribed yet", "Sounds with audio that haven't been transcribed"),
            ("smart:instrumental", "Instrumentals", "Transcribed, but no speech was detected"),
            ("smart:missing_audio", "Missing audio", "Sounds with no local audio"),
            ("smart:high_popularity", "100K+ uses", "Popular sounds"),
            ("smart:has_videos", "Has example videos", "Sounds with associated videos"),
        ]
        for target_id, label, tooltip in sidebar_rows:
            button = LibraryDropButton(label, target_id, self.library_section)
            button.setObjectName("libraryBinButton")
            button.setToolTip(tooltip)
            button.clicked.connect(lambda _checked=False, value=target_id: self.apply_library_filter(value))
            button.droppedMusicId.connect(self.handle_library_drop)
            self.library_bin_buttons[target_id] = button
            self.library_section_layout.addWidget(button)
        for bin_row in self.vm.library_bins():
            button = LibraryDropButton(f"▣ {bin_row.name}", f"bin:{bin_row.id}", self.library_section)
            button.setObjectName("libraryBinButton")
            button.setToolTip(f"Sorting bin: {bin_row.name}; drop rows here to add")
            button.clicked.connect(lambda _checked=False, value=f"bin:{bin_row.id}": self.apply_library_filter(value))
            button.droppedMusicId.connect(self.handle_library_drop)
            self.library_bin_buttons[f"bin:{bin_row.id}"] = button
            self.library_section_layout.addWidget(button)
        self.library_section_layout.addStretch(1)
        self._sync_library_sidebar_active_state()

    def _sync_library_sidebar_active_state(self) -> None:
        for target_id, button in getattr(self, "library_bin_buttons", {}).items():
            button.setProperty("active", target_id == self.active_library_filter)
            button.style().unpolish(button)
            button.style().polish(button)

    def apply_library_filter(self, value: str) -> None:
        self.active_library_filter = value
        self.show_view("library")
        if value == "smart:needs_transcript":
            self._set_combo_by_data(self.media_filter, "pending_transcript")
        elif value == "smart:instrumental":
            self._set_combo_by_data(self.media_filter, "empty_transcript")
        elif value == "smart:missing_audio":
            self._set_combo_by_data(self.media_filter, "missing_audio")
        elif value == "smart:high_popularity":
            self._set_combo_by_data(self.usage_filter, "over_100k")
        elif value == "smart:has_videos":
            self._set_combo_by_data(self.media_filter, "has_videos")
        elif value in {"all", "favorites"} or value.startswith("bin:"):
            self._set_combo_by_data(self.media_filter, "all")
            self._set_combo_by_data(self.usage_filter, "all")
        self._sync_library_sidebar_active_state()
        self.refresh_table()
        self.statusBar().showMessage(self._library_filter_label(value), 2200)

    def create_sorting_bin(
        self,
        *,
        seed_music_id: str | None = None,
        seed_music_ids: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        name, ok = QInputDialog.getText(self, "New sorting bin", "Bin name:")
        if not ok or not name.strip():
            return
        try:
            bin_row = self.vm.create_library_bin(name)
        except ValueError as exc:
            QMessageBox.warning(self, "Could not create bin", str(exc))
            return
        seeded_ids = self._dedupe_music_ids([*(seed_music_ids or ()), seed_music_id or ""])
        for music_id in seeded_ids:
            self.vm.add_to_library_bin(bin_row.id, music_id)
        self.refresh_library_sidebar()
        self.apply_library_filter(f"bin:{bin_row.id}")
        count_note = f" • added {len(seeded_ids)} selected" if seeded_ids else ""
        self.statusBar().showMessage(f"Created sorting bin: {bin_row.name}{count_note}", 3000)

    def handle_library_drop(self, target_id: str, music_id: str) -> None:
        self.add_music_ids_to_library_target(target_id, (music_id,))

    def add_music_ids_to_library_target(self, target_id: str, music_ids: tuple[str, ...] | list[str]) -> None:
        ids = self._dedupe_music_ids(music_ids)
        if not ids:
            return
        if target_id == "favorites":
            for music_id in ids:
                self.vm.collections.add_favorite(music_id)
            self.statusBar().showMessage(f"Added {len(ids)} sound(s) to Favorites", 2500)
        elif target_id.startswith("bin:"):
            bin_id = target_id.removeprefix("bin:")
            added = 0
            for music_id in ids:
                if self.vm.add_to_library_bin(bin_id, music_id):
                    added += 1
            if added:
                self.statusBar().showMessage(f"Added {added} sound(s) to sorting bin", 2500)
        self.refresh_library_sidebar()
        self.refresh_table()

    def _build_jukebox_chrome(self) -> QWidget:
        chrome = QFrame()
        chrome.setObjectName("chromeHeader")
        layout = QHBoxLayout(chrome)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(14)

        transport = QFrame()
        transport.setObjectName("transportDeck")
        transport_layout = QHBoxLayout(transport)
        transport_layout.setContentsMargins(9, 8, 9, 8)
        transport_layout.setSpacing(7)
        transport_actions = [
            ("◀◀", self.select_previous_sound, "Previous sound"),
            ("▶", self.toggle_play_pause, "Play / pause"),
            ("▶▶", self.select_next_sound, "Next sound"),
        ]
        self.transport_buttons = {}
        for label, handler, tooltip in transport_actions:
            button = QToolButton()
            button.setText(label)
            button.setObjectName("transportButton")
            button.setToolTip(tooltip)
            button.clicked.connect(handler)
            self.transport_buttons[label] = button
            transport_layout.addWidget(button)
        self.transport_play_button = self.transport_buttons["▶"]
        self.continuous_play_button = QToolButton()
        self.continuous_play_button.setText("CONT")
        self.continuous_play_button.setObjectName("transportButton")
        self.continuous_play_button.setCheckable(True)
        self.continuous_play_button.setToolTip("Continuous play through the current library order")
        self.continuous_play_button.clicked.connect(self.toggle_continuous_play)
        transport_layout.addWidget(self.continuous_play_button)
        self.random_play_button = QToolButton()
        self.random_play_button.setText("RND")
        self.random_play_button.setObjectName("transportButton")
        self.random_play_button.setToolTip("Play a random sound from the current library")
        self.random_play_button.clicked.connect(self.play_random_sound)
        transport_layout.addWidget(self.random_play_button)
        layout.addWidget(transport)

        display = QFrame()
        display.setObjectName("capsuleDisplay")
        display.setFixedHeight(56)
        # Shrinkable: the now-playing title elides (Ignored hpolicy), so the capsule
        # can give up width on small windows instead of forcing the whole row wider.
        display.setMinimumWidth(140)
        display_layout = QVBoxLayout(display)
        display_layout.setContentsMargins(18, 8, 18, 8)
        display_layout.setSpacing(2)
        now_playing = QLabel("NOW PLAYING")
        now_playing.setObjectName("displayEyebrow")
        self.now_playing_title = QLabel("Nothing playing")
        self.now_playing_title.setObjectName("displayTitle")
        self.now_playing_title.setWordWrap(False)
        self.now_playing_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        display_layout.addWidget(now_playing)
        display_layout.addWidget(self.now_playing_title)
        layout.addWidget(display, 1)

        self.portal_tabs = {}

        status = QFrame()
        status.setObjectName("limeStatusPanel")
        status_layout = QHBoxLayout(status)
        status_layout.setContentsMargins(10, 7, 10, 7)
        status_layout.setSpacing(7)
        self.index_status_dot = StatusDot()
        self.index_status_text = QLabel("INDEX WAITING")
        self.index_status_text.setObjectName("statusReadout")
        status_layout.addWidget(self.index_status_dot)
        status_layout.addWidget(self.index_status_text)
        layout.addWidget(status)
        return chrome

    def _build_library_view(self) -> QWidget:
        view = QWidget()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        search_bar = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setObjectName("searchBox")
        self.search_box.setPlaceholderText("Search title, artist, tag, music ID, spoken phrase…")
        self.search_box.textChanged.connect(self.schedule_refresh_table)
        self.duration_filter = QComboBox()
        self.duration_filter.addItem("All durations", "all")
        self.duration_filter.addItem("Under 30s", "under_30")
        self.duration_filter.addItem("30s and up", "30_plus")
        self.duration_filter.currentIndexChanged.connect(self.schedule_refresh_table)
        self.media_filter = QComboBox()
        self.media_filter.addItem("All media", "all")
        self.media_filter.addItem("Has audio", "has_audio")
        self.media_filter.addItem("Missing audio", "missing_audio")
        self.media_filter.addItem("Has artwork", "has_artwork")
        self.media_filter.addItem("Missing artwork", "missing_artwork")
        self.media_filter.addItem("Has transcript", "has_transcript")
        self.media_filter.addItem("Missing transcript (any)", "missing_transcript")
        self.media_filter.addItem("  ↳ Not transcribed yet", "pending_transcript")
        self.media_filter.addItem("  ↳ Instrumental (no speech)", "empty_transcript")
        self.media_filter.addItem("Has associated videos", "has_videos")
        self.media_filter.addItem("Missing associated videos", "missing_videos")
        self.media_filter.addItem("Has evidence", "has_evidence")
        self.media_filter.addItem("Missing evidence", "missing_evidence")
        self.media_filter.currentIndexChanged.connect(self.schedule_refresh_table)
        self.status_filter = QComboBox()
        self.status_filter.addItem("All statuses", "all")
        self.status_filter.addItem("Approved", "approved")
        self.status_filter.addItem("Needs review", "needs_review")
        self.status_filter.addItem("Unreviewed", "unreviewed")
        self.status_filter.currentIndexChanged.connect(self.schedule_refresh_table)
        self.usage_filter = QComboBox()
        self.usage_filter.addItem("All usage", "all")
        self.usage_filter.addItem("Unknown usage", "unknown_usage")
        self.usage_filter.addItem("Under 1K uses", "under_1k")
        self.usage_filter.addItem("1K+ uses", "over_1k")
        self.usage_filter.addItem("100K+ uses", "over_100k")
        self.usage_filter.addItem("1M+ uses", "over_1m")
        self.usage_filter.currentIndexChanged.connect(self.schedule_refresh_table)
        self.column_menu = QMenu("Columns", self)
        columns_button = QToolButton()
        columns_button.setText("Columns ▾")
        columns_button.setObjectName("columnsButton")
        columns_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        columns_button.setMenu(self.column_menu)
        self.result_count_label = QLabel("0 displayed / 0 indexed")
        self.result_count_label.setObjectName("muted")
        # Two rows so the controls collapse gracefully at small widths instead of
        # forcing the whole window wide: search box on top (fills width), filters +
        # column picker + count below (shrink to their minimums, count pinned right).
        self.search_box.setMinimumWidth(150)
        self.search_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for _combo in (self.duration_filter, self.media_filter, self.status_filter, self.usage_filter):
            _combo.setMinimumWidth(92)
        self.result_count_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        search_bar.addWidget(self.search_box, 1)
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(8)
        filter_bar.addWidget(self.duration_filter)
        filter_bar.addWidget(self.media_filter)
        filter_bar.addWidget(self.status_filter)
        filter_bar.addWidget(self.usage_filter)
        filter_bar.addWidget(columns_button)
        filter_bar.addStretch(1)
        filter_bar.addWidget(self.result_count_label)
        layout.addLayout(search_bar)
        layout.addLayout(filter_bar)
        stats_grid = QGridLayout()
        self.stats_label = QLabel("index not built")
        self.stats_label.setObjectName("statCard")
        self.inbox_label = QLabel("Shortcut inbox\n0 pending")
        self.inbox_label.setObjectName("statCard")
        self.worker_label = QLabel("Worker\nidle")
        self.worker_label.setObjectName("statCard")
        self.catalog_label = QLabel("Catalog\nrows vs unique IDs pending")
        self.catalog_label.setObjectName("statCard")
        stats_grid.addWidget(self.stats_label, 0, 0)
        stats_grid.addWidget(self.inbox_label, 0, 1)
        stats_grid.addWidget(self.worker_label, 0, 2)
        stats_grid.addWidget(self.catalog_label, 0, 3)
        layout.addLayout(stats_grid)
        self.table = SoundTableView()
        library_model = self.table.library_model()
        library_model.audio_path_resolver = self._drag_audio_path
        library_model.usage_formatter = self._format_usage_count
        self._prepare_table(self.table, stretch_column=SOUND_COL)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(False)  # custom Python sort via header-click handler
        self.table.setItemDelegateForColumn(FAVORITE_COL, FavoriteButtonDelegate(self.toggle_favorite_by_id, self.table))
        self.play_delegate = PlayButtonDelegate(self.play_record_by_id, self.table)
        self.table.setItemDelegateForColumn(PLAY_COL, self.play_delegate)
        self.table.setDragEnabled(True)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.table.setDragDropOverwriteMode(False)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSortIndicator(self.library_sort_column, self.library_sort_order)
        self.configure_column_menu()
        self.restore_hidden_columns()
        self.restore_table_layout("library", self.table)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self.update_preview_from_selection())
        self.table.doubleClicked.connect(lambda _index: self.play_selected_sound())
        self.table.horizontalHeader().sectionClicked.connect(self.handle_library_header_clicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.open_library_context_menu)
        layout.addWidget(self.table, 1)
        return view

    def _build_inbox_view(self) -> QWidget:
        view = QWidget()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        inbox_title = QLabel("Shortcut inbox")
        inbox_title.setObjectName("sectionTitle")
        refresh_inbox = QPushButton("Refresh inbox")
        refresh_inbox.clicked.connect(self.refresh_inbox)
        download_import = QPushButton("Download && import")
        download_import.setObjectName("primaryButton")
        download_import.clicked.connect(self.download_and_import)
        poll_relay = QPushButton("Poll relay")
        poll_relay.clicked.connect(self.poll_relay_inbox)
        import_pack = QPushButton("Import sound pack")
        import_pack.setToolTip("Import a Sound Cache sound-pack JSON or a TikTok data export (favorites) into the inbox, then Download & import")
        import_pack.clicked.connect(self.import_sound_pack)
        import_export = QPushButton("Import TikTok export")
        import_export.clicked.connect(self.import_tiktok_favorite_sounds_export)
        mark_imported = QPushButton("Mark selected imported")
        mark_imported.clicked.connect(self.mark_selected_inbox_imported)
        header.addWidget(inbox_title)
        header.addStretch(1)
        header.addWidget(download_import)
        header.addWidget(import_pack)
        header.addWidget(import_export)
        header.addWidget(poll_relay)
        header.addWidget(refresh_inbox)
        header.addWidget(mark_imported)
        layout.addLayout(header)
        self.inbox_table = QTableWidget(0, 5)
        self.inbox_table.setHorizontalHeaderLabels(["received", "source", "url", "status", "error"])
        self._prepare_table(self.inbox_table, stretch_column=2)
        self.restore_table_layout("inbox_v2", self.inbox_table)
        layout.addWidget(self.inbox_table, 1)
        return view

    def _build_review_view(self) -> QWidget:
        view = QWidget()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        review_title = QLabel("Review queues")
        review_title.setObjectName("sectionTitle")
        refresh_review = QPushButton("Refresh review queues")
        refresh_review.clicked.connect(self.refresh_review_queues)
        header.addWidget(review_title)
        header.addStretch(1)
        header.addWidget(refresh_review)
        layout.addLayout(header)
        self.review_table = QTableWidget(0, 3)
        self.review_table.setHorizontalHeaderLabels(["queue", "count", "next action"])
        self.review_table.itemDoubleClicked.connect(self.apply_review_queue_filter)
        self._prepare_table(self.review_table, stretch_column=2)
        layout.addWidget(self.review_table, 1)
        return view

    def _build_dedupe_view(self) -> QWidget:
        view = QWidget()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        dedupe_title = QLabel("Duplicate review")
        dedupe_title.setObjectName("sectionTitle")
        refresh_dedupe = QPushButton("Refresh duplicate candidates")
        refresh_dedupe.clicked.connect(self.refresh_dedupe_review)
        mark_duplicates = QPushButton("Mark duplicates")
        mark_duplicates.clicked.connect(lambda: self.record_selected_duplicate_decision("duplicates"))
        mark_not_duplicates = QPushButton("Mark not duplicates")
        mark_not_duplicates.clicked.connect(lambda: self.record_selected_duplicate_decision("not_duplicates"))
        quarantine_duplicates = QPushButton("Quarantine duplicates")
        quarantine_duplicates.setObjectName("dangerButton")
        quarantine_duplicates.setToolTip(
            "Keeps the selected candidate in the vault and moves only the other duplicate folders to reports/duplicate-quarantine."
        )
        quarantine_duplicates.clicked.connect(self.quarantine_selected_duplicates)
        header.addWidget(dedupe_title)
        header.addStretch(1)
        header.addWidget(refresh_dedupe)
        header.addWidget(mark_duplicates)
        header.addWidget(mark_not_duplicates)
        header.addWidget(quarantine_duplicates)
        layout.addLayout(header)
        tables = QHBoxLayout()
        self.dedupe_groups_table = QTableWidget(0, 4)
        self.dedupe_groups_table.setHorizontalHeaderLabels(["group", "score", "candidates", "reason"])
        self.dedupe_groups_table.itemSelectionChanged.connect(self.refresh_dedupe_candidates)
        self._prepare_table(self.dedupe_groups_table, stretch_column=3)
        self.dedupe_candidates_table = QTableWidget(0, 5)
        self.dedupe_candidates_table.setHorizontalHeaderLabels(["play", "music id", "title", "artist", "audio/folder"])
        self.dedupe_candidates_table.itemSelectionChanged.connect(self.update_preview_from_dedupe_selection)
        self._prepare_table(self.dedupe_candidates_table, stretch_column=2)
        tables.addWidget(self.dedupe_groups_table, 1)
        tables.addWidget(self.dedupe_candidates_table, 2)
        layout.addLayout(tables, 1)
        return view

    def _build_worker_view(self) -> QWidget:
        view = QWidget()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        worker_title = QLabel("Worker status")
        worker_title.setObjectName("sectionTitle")
        refresh_worker = QPushButton("Refresh worker status")
        refresh_worker.clicked.connect(self.refresh_worker_status)
        reenrich = QPushButton("Re-enrich incomplete…")
        reenrich.setObjectName("primaryButton")
        reenrich.setToolTip(
            "Re-run metadata enrichment (page scrape + oEmbed) on sounds in the vault that are "
            "missing artist, artwork, or popularity. Updates them in place — no re-download."
        )
        reenrich.clicked.connect(self.reenrich_incomplete_metadata)
        enrich_oembed = QPushButton("Enrich from data export…")
        enrich_oembed.setToolTip(
            "Batch-enrich a TikTok 'favorite sounds' data-export JSON via oEmbed (opens a file picker). "
            "For re-enriching sounds already in your vault, use “Re-enrich incomplete”."
        )
        enrich_oembed.clicked.connect(self.run_oembed_enrichment)
        package_import = QPushButton("Package imported metadata")
        package_import.clicked.connect(self.package_imported_metadata)
        header.addWidget(worker_title)
        header.addStretch(1)
        header.addWidget(reenrich)
        header.addWidget(enrich_oembed)
        header.addWidget(package_import)
        header.addWidget(refresh_worker)
        layout.addLayout(header)
        self.worker_status_table = QTableWidget(0, 2)
        self.worker_status_table.setHorizontalHeaderLabels(["metric", "value"])
        self._prepare_table(self.worker_status_table, stretch_column=1)
        layout.addWidget(self.worker_status_table, 1)
        return view

    def _build_preview_panel(self) -> QFrame:
        preview = QFrame()
        preview.setObjectName("preview")
        # Anchor the panel to the artwork width so it never needs a horizontal
        # scrollbar; everything inside stacks to this width.
        preview.setMinimumWidth(250)
        preview.setMaximumWidth(340)
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(20, 22, 20, 22)
        preview_layout.setSpacing(10)
        self.artwork_label = QLabel("no artwork")
        self.artwork_label.setObjectName("artwork")
        self.artwork_label.setFixedSize(210, 210)
        self.artwork_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll = QScrollArea()
        scroll.setObjectName("inspectorScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Never span horizontally — content wraps/stacks to the panel width.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_body = QWidget()
        scroll_layout = QVBoxLayout(scroll_body)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(10)
        self.preview_title = QLabel("Select a sound")
        self.preview_title.setObjectName("previewTitle")
        self.preview_title.setWordWrap(True)
        self._make_label_selectable(self.preview_title)
        self.preview_meta = QLabel("Audio, tags, evidence and local paths will show here.", scroll_body)
        self.preview_meta.setObjectName("muted")
        self.preview_meta.setWordWrap(True)
        self._make_label_selectable(self.preview_meta)
        self.preview_tags = QLabel("", scroll_body)
        self.preview_tags.setWordWrap(True)
        self._make_label_selectable(self.preview_tags)
        self.progress_slider = SeekSlider(Qt.Orientation.Horizontal)
        self.progress_slider.setRange(0, 0)
        # While dragging, only preview the target time; commit the seek on
        # release (a restart-based backend can't take a seek per drag tick).
        # A click on the groove is a single discrete seek.
        self.progress_slider.sliderMoved.connect(self._preview_seek_time)
        self.progress_slider.sliderReleased.connect(self._commit_slider_seek)
        self.progress_slider.seekRequested.connect(self.seek_playback)
        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setObjectName("muted")
        self._make_label_selectable(self.time_label)
        self.playback_status = QLabel("Playback idle", scroll_body)
        self.playback_status.setObjectName("muted")
        self.playback_status.setWordWrap(True)
        self._make_label_selectable(self.playback_status)
        self.copy_metadata = QPushButton("Copy metadata")
        self.copy_metadata.setEnabled(False)
        self.copy_metadata.clicked.connect(self.copy_selected_metadata)
        self.open_tiktok_sound = QPushButton("Open TikTok sound")
        self.open_tiktok_sound.setEnabled(False)
        self.open_tiktok_sound.clicked.connect(self.open_selected_tiktok_sound)
        # Shown only for sounds TikTok linked to a published Spotify track.
        self.open_spotify = QPushButton("Open in Spotify")
        self.open_spotify.setObjectName("spotifyButton")
        self.open_spotify.setVisible(False)
        self.open_spotify.clicked.connect(self.open_selected_spotify)
        self.open_folder = QPushButton("Open folder")
        self.open_folder.setEnabled(False)
        self.open_folder.clicked.connect(self.open_selected_folder)
        # Transcript + notes grow/shrink with the panel height (stretch factors
        # below) instead of being pinned to a fixed height.
        transcript_group = QGroupBox("Transcript")
        transcript_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        transcript_layout = QVBoxLayout(transcript_group)
        self.transcript_text = QTextEdit()
        self.transcript_text.setReadOnly(True)
        self.transcript_text.setMinimumHeight(110)
        self.transcript_text.setPlaceholderText(self._DEFAULT_TRANSCRIPT_PLACEHOLDER)
        transcript_layout.addWidget(self.transcript_text)
        notes_group = QGroupBox("User notes")
        notes_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        notes_layout = QVBoxLayout(notes_group)
        self.user_notes_edit = QTextEdit()
        self.user_notes_edit.setMinimumHeight(70)
        self.user_notes_edit.setPlaceholderText(
            "Add your own notes — a label, why you saved it, where to use it… (saved + searchable)"
        )
        self.user_notes_edit.textChanged.connect(self._on_user_notes_changed)
        notes_layout.addWidget(self.user_notes_edit)
        evidence_group = QGroupBox("Evidence assets", scroll_body)
        evidence_layout = QVBoxLayout(evidence_group)
        self.evidence_list = QLabel("No local screenshots yet.", evidence_group)
        self.evidence_list.setWordWrap(True)
        self._make_label_selectable(self.evidence_list)
        evidence_layout.addWidget(self.evidence_list)
        video_group = QGroupBox("Associated videos", scroll_body)
        video_layout = QVBoxLayout(video_group)
        self.video_table = QTableWidget(0, 4, video_group)
        self.video_table.setHorizontalHeaderLabels(["rank", "creator", "clip", "notes"])
        self._prepare_table(self.video_table, stretch_column=3)
        self.video_table.setMaximumHeight(170)
        self.video_table.itemDoubleClicked.connect(self.open_associated_video_from_item)
        video_layout.addWidget(self.video_table)
        video_actions = QHBoxLayout()
        self.open_video = QPushButton("Open video")
        self.open_video.setEnabled(False)
        self.open_video.clicked.connect(self.open_selected_associated_video)
        self.open_video_url = QPushButton("Open URL")
        self.open_video_url.setEnabled(False)
        self.open_video_url.clicked.connect(lambda: self.open_selected_associated_video(prefer_url=True))
        video_actions.addWidget(self.open_video, 1)
        video_actions.addWidget(self.open_video_url, 1)
        video_layout.addLayout(video_actions)
        # Stack the action buttons full-width so they stay readable at the narrow
        # artwork-width panel (a 3-across row was what forced the horizontal scroll).
        action_col = QVBoxLayout()
        action_col.setSpacing(6)
        action_col.addWidget(self.copy_metadata)
        action_col.addWidget(self.open_tiktok_sound)
        action_col.addWidget(self.open_spotify)
        action_col.addWidget(self.open_folder)
        for _btn in (self.copy_metadata, self.open_tiktok_sound, self.open_spotify, self.open_folder, self.open_video, self.open_video_url):
            _btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            _btn.setMinimumHeight(32)
        scroll_layout.addWidget(self.artwork_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        scroll_layout.addWidget(self.preview_title)
        scroll_layout.addWidget(self.progress_slider)
        scroll_layout.addWidget(self.time_label)
        scroll_layout.addLayout(action_col)
        scroll_layout.addWidget(transcript_group, 3)
        scroll_layout.addWidget(notes_group, 2)
        for hidden in (self.preview_meta, self.preview_tags, self.playback_status, evidence_group, video_group):
            hidden.setVisible(False)
        scroll.setWidget(scroll_body)
        preview_layout.addWidget(scroll, 1)
        return preview

    def setup_keyboard_shortcuts(self) -> None:
        find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        find_shortcut.activated.connect(self.focus_search)
        copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self)
        copy_shortcut.activated.connect(self.copy_selected_metadata)
        play_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        play_shortcut.activated.connect(self.play_selected_sound)
        escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape_shortcut.activated.connect(self.clear_search)

    def focus_search(self) -> None:
        self.search_box.setFocus()
        self.search_box.selectAll()
        self.statusBar().showMessage("Search focused", 1500)

    def clear_search(self) -> None:
        if self.search_box.text():
            self.search_box.clear()
            self.statusBar().showMessage("Search cleared", 1500)

    def reset_library_filters(self) -> None:
        self.active_library_filter = "all"
        self.search_box.clear()
        for combo in (self.duration_filter, self.media_filter, self.status_filter, self.usage_filter):
            self._set_combo_by_data(combo, "all")
        self._sync_library_sidebar_active_state()
        self.statusBar().showMessage("Library filters reset", 1500)

    def _set_media_filter(self, value: str) -> None:
        self.show_view("library")
        self._set_combo_by_data(self.media_filter, value)
        self.refresh_table()
        self.statusBar().showMessage(f"Filter bank engaged: {value.replace('_', ' ')}", 2200)

    def _set_usage_filter(self, value: str) -> None:
        self.show_view("library")
        self._set_combo_by_data(self.usage_filter, value)
        self.refresh_table()
        self.statusBar().showMessage(f"Popularity filter engaged: {value.replace('_', ' ')}", 2200)

    def refresh_portal_tabs(self) -> None:
        if not hasattr(self, "portal_tabs"):
            return
        try:
            health = self.vm.db.archive_health_counts()
            total = health["total"]
            evidence = total - health["missing_evidence"]
            transcripts = total - health["missing_transcript"]
            # Cheap COUNT instead of building every 100k+ record just to len() it.
            popularity = self.vm.db.count_usage_at_least(100_000)
        except Exception:
            return
        if hasattr(self, "archive_health_panel"):
            self.archive_health_panel.update_from_counts(health)
        labels = {
            "LIBRARY": f"LIBRARY ({total:,})",
            "EVIDENCE": f"EVIDENCE ({evidence:,})",
            "TRANSCRIPTS": f"TRANSCRIPTS ({transcripts:,})",
            "POPULARITY": f"POPULARITY ({popularity:,})",
        }
        for key, label in labels.items():
            if key in self.portal_tabs:
                self.portal_tabs[key].setText(label)

    def _set_index_status(self, text: str, *, state: str = "waiting") -> None:
        if hasattr(self, "index_status_text"):
            self.index_status_text.setText(text)
        if hasattr(self, "index_status_dot"):
            self.index_status_dot.set_state(state)

    def select_previous_sound(self) -> None:
        was_playing = self._is_audio_playing()
        row = max(0, self.table.currentRow() - 1)
        if self.table.rowCount():
            self.table.selectRow(row)
            self.update_preview_from_selection()
            if was_playing:
                self.play_selected_sound()

    def select_next_sound(self) -> None:
        if not self.table.rowCount():
            return
        was_playing = self._is_audio_playing()
        row = min(self.table.rowCount() - 1, self.table.currentRow() + 1)
        self.table.selectRow(row)
        self.update_preview_from_selection()
        if was_playing:
            self.play_selected_sound()

    def toggle_continuous_play(self) -> None:
        self.continuous_play_enabled = self.continuous_play_button.isChecked()
        state = "on" if self.continuous_play_enabled else "off"
        self.statusBar().showMessage(f"Continuous play {state}", 2200)

    def play_random_sound(self) -> None:
        rows = self._playable_library_rows()
        if not rows:
            self.playback_status.setText("No playable sounds in the current library view")
            self.statusBar().showMessage("No playable sounds in the current library view", 2500)
            return
        current_row = self.table.currentRow()
        if len(rows) > 1 and current_row in rows:
            rows = [row for row in rows if row != current_row]
        row = random.choice(rows)
        self._select_library_row(row, play=True)
        self.statusBar().showMessage("Playing random sound", 2200)

    def _playable_library_rows(self) -> list[int]:
        rows: list[int] = []
        for row in range(self.table.rowCount()):
            music_id = self.table.music_id_at(row) or ""
            record = self.records_by_id.get(str(music_id))
            if record is not None and self._record_has_playable_pointer(record):
                rows.append(row)
        return rows

    def _select_library_row(self, row: int, *, play: bool = False) -> None:
        if row < 0 or row >= self.table.rowCount():
            return
        self.table.selectRow(row)
        self.update_preview_from_selection()
        if play:
            self.play_selected_sound()

    def _play_next_continuous_sound(self) -> None:
        if not self.continuous_play_enabled:
            return
        current_row = self.table.currentRow()
        for row in self._playable_library_rows():
            if row > current_row:
                self._select_library_row(row, play=True)
                return
        self.playback_status.setText("Continuous playback reached the end of the current library view")
        self.statusBar().showMessage("Continuous playback reached the end", 2500)

    def _is_audio_playing(self) -> bool:
        if self._is_external_audio_playing():
            return True
        if self.audio_player is None:
            return False
        from PySide6.QtMultimedia import QMediaPlayer

        return self.audio_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def _is_external_audio_playing(self) -> bool:
        return self.external_audio_process is not None and self.external_audio_process.poll() is None

    def _should_use_external_audio(self, target: Path | str) -> bool:
        if not isinstance(target, Path):
            return False
        if _env_flag("SOUND_VAULT_DISABLE_AFPLAY"):
            return False
        if platform.system() != "Darwin":
            return False
        return shutil.which("ffplay") is not None or shutil.which("afplay") is not None

    @staticmethod
    def _external_player_command(target: Path, start_ms: int) -> tuple[list[str] | None, bool]:
        """Pick the macOS external player and build its argv.

        ``ffplay`` (ffmpeg) is preferred because it can start at an offset
        (``-ss``), which is how we implement seeking — afplay has no seek and
        always plays from the start. Returns ``(argv, seek_supported)``.
        """
        ffplay = shutil.which("ffplay")
        if ffplay is not None:
            cmd = [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet"]
            if start_ms > 0:
                cmd += ["-ss", f"{start_ms / 1000:.3f}"]
            cmd.append(str(target))
            return cmd, True
        afplay = shutil.which("afplay")
        if afplay is not None:
            return [afplay, str(target)], False
        return None, False

    def _stop_external_audio(self) -> None:
        process = self.external_audio_process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
        self.external_audio_process = None
        self.external_audio_target = None
        self._external_audio_started_at = None
        self._external_audio_base_offset_ms = 0
        self._external_audio_timer.stop()

    def _play_external_audio(self, target: Path, start_ms: int = 0) -> bool:
        start_ms = max(0, int(start_ms))
        command, seek_supported = self._external_player_command(target, start_ms)
        if command is None:
            return False
        self._stop_external_audio()
        try:
            self.external_audio_process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            write_event("gui.external_audio_exception", target=str(target), **exception_fields(exc))
            self.playback_status.setText(f"macOS audio fallback failed: {exc}")
            self.external_audio_process = None
            self.external_audio_target = None
            return False
        backend = "ffplay" if seek_supported else "afplay"
        self.external_audio_target = target
        # External players report no position, so estimate the scrubber from
        # elapsed wall time, anchored at the offset we started playing from.
        self._external_audio_started_at = time.monotonic()
        self._external_audio_base_offset_ms = start_ms
        self._external_seek_supported = seek_supported
        record = self.current_preview_record
        duration_ms = self._duration_ms_for_record(record) if record is not None else 0
        if duration_ms <= 0:
            # No duration in metadata (common for bulk imports) — probe the file so
            # the scrubber is usable and the timestamp shows the real total.
            duration_ms = self._probe_audio_duration_ms(target)
        self._external_audio_duration_ms = duration_ms
        if self._external_audio_duration_ms > 0:
            self.progress_slider.setRange(0, self._external_audio_duration_ms)
            self.progress_slider.setValue(min(start_ms, self._external_audio_duration_ms))
        self._external_audio_timer.start()
        write_event("gui.external_audio_started", target=str(target), backend=backend, start_ms=str(start_ms))
        self.playback_status.setText(f"Playing {target.name} via macOS audio")
        return True

    def _update_external_progress(self) -> None:
        started = self._external_audio_started_at
        if started is None:
            return
        elapsed_ms = self._external_audio_base_offset_ms + int((time.monotonic() - started) * 1000)
        duration = self._external_audio_duration_ms
        if duration > 0:
            position = min(elapsed_ms, duration)
            if not self.progress_slider.isSliderDown():
                self.progress_slider.setValue(position)
            self.time_label.setText(f"{self._format_ms(position)} / {self._format_ms(duration)}")
        else:
            self.time_label.setText(self._format_ms(elapsed_ms))

    def _poll_external_audio(self) -> None:
        process = self.external_audio_process
        if process is None:
            return
        if process.poll() is None:
            # still playing — advance the estimated scrubber/time
            self._update_external_progress()
            return
        code = process.returncode
        target = self.external_audio_target
        self.external_audio_process = None
        self.external_audio_target = None
        self._external_audio_started_at = None
        self._external_audio_base_offset_ms = 0
        self._external_audio_timer.stop()
        if self._external_audio_duration_ms > 0:
            self.progress_slider.setValue(self._external_audio_duration_ms)
        write_event("gui.external_audio_finished", target=str(target or ""), returncode=code)
        self._set_playing(None)
        if hasattr(self, "transport_play_button"):
            self.transport_play_button.setText("▶")
            self.transport_play_button.setToolTip("Play selected sound")
        # afplay reports no EndOfMedia signal, so drive continuous play from here.
        if self.continuous_play_enabled:
            QTimer.singleShot(0, self._play_next_continuous_sound)

    def _ensure_audio_player(self) -> bool:
        if self.audio_player is not None:
            return True
        if _env_flag("SOUND_VAULT_SAFE_MODE") or _env_flag("SOUND_VAULT_DISABLE_AUDIO"):
            self.playback_status.setText("Playback engine disabled by safe mode")
            write_event("gui.audio_player_disabled")
            return False
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

            self.audio_output = QAudioOutput(self)
            self.audio_player = QMediaPlayer(self)
            self.audio_player.setAudioOutput(self.audio_output)
            self.audio_output.setVolume(0.85)
            self.audio_player.positionChanged.connect(self._player_position_changed)
            self.audio_player.durationChanged.connect(self._player_duration_changed)
            self.audio_player.playbackStateChanged.connect(self._player_state_changed)
            self.audio_player.mediaStatusChanged.connect(self._player_media_status_changed)
            self.audio_player.errorOccurred.connect(self._player_error_occurred)
            write_event("gui.audio_player_ready")
            return True
        except Exception as exc:
            write_event("gui.audio_player_exception", **exception_fields(exc))
            self.playback_status.setText(f"Playback engine unavailable: {exc}")
            return False

    def _preview_seek_time(self, position: int) -> None:
        """During a drag, show the target time without committing the seek."""
        duration = self._external_audio_duration_ms
        if duration <= 0 and self.audio_player is not None:
            duration = self.audio_player.duration()
        if duration > 0:
            self.time_label.setText(f"{self._format_ms(int(position))} / {self._format_ms(duration)}")

    def _commit_slider_seek(self) -> None:
        """Drag finished: seek to the handle's final position."""
        self.seek_playback(self.progress_slider.value())

    def seek_playback(self, position: int) -> None:
        position = max(0, int(position))
        # External (macOS) backend: ffplay/afplay can't seek in place, so restart
        # the player at the requested offset. Only ffplay honours an offset; with
        # afplay-only we can't seek, so leave playback where it is.
        if self.external_audio_target is not None:
            if not self._external_seek_supported:
                self.playback_status.setText("Seeking needs ffmpeg (ffplay) — install it to scrub macOS audio")
                self._update_external_progress()  # snap the handle back to real position
                return
            # Ignore negligible seeks (e.g. just grabbing the handle) so we don't
            # kill+respawn the player for a sub-half-second move.
            if self._external_audio_started_at is not None:
                current = self._external_audio_base_offset_ms + int(
                    (time.monotonic() - self._external_audio_started_at) * 1000
                )
                if abs(position - current) < 400:
                    return
            target = self.external_audio_target
            if self._play_external_audio(target, start_ms=position) and self.current_preview_record is not None:
                self._set_playing(self.current_preview_record.music_id)
                if hasattr(self, "transport_play_button"):
                    self.transport_play_button.setText("▮▮")
                    self.transport_play_button.setToolTip("Stop playback")
            return
        if self.audio_player is not None:
            self.audio_player.setPosition(position)

    def pause_playback(self) -> None:
        self._stop_external_audio()
        if self.audio_player is not None:
            self.audio_player.pause()
        self.playback_status.setText("Playback paused")
        self._set_playing(None)

    def toggle_play_pause(self) -> None:
        if self._is_audio_playing():
            self.pause_playback()
        else:
            self.play_selected_sound()

    def _set_playing(self, music_id: str | None) -> None:
        """Mark which library row is playing so its ▶ button shows ▮▮. Cheap repaint, no rebuild."""
        music_id = str(music_id) if music_id else None
        if music_id == self.playing_music_id:
            return
        self.playing_music_id = music_id
        delegate = getattr(self, "play_delegate", None)
        if delegate is not None:
            delegate.playing_music_id = music_id
            if hasattr(self, "table"):
                self.table.viewport().update()

    def _drag_audio_path(self, music_id: str) -> Path | None:
        """Local audio file for a dragged row, so dragging out copies the real sound."""
        record = self.records_by_id.get(str(music_id))
        if record is None:
            return None
        audio = getattr(record, "local_audio_path", None)
        if audio is not None and Path(audio).exists():
            return Path(audio)
        return None

    def _prepare_table(self, table: QTableWidget, *, stretch_column: int) -> None:
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSortingEnabled(True)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        # Let the deck's sparkle backdrop show through the table (the QSS paints the
        # rows transparent/translucent; the viewport must not auto-fill an opaque base).
        table.viewport().setAutoFillBackground(False)
        header = table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setStretchLastSection(False)
        for idx in range(table.columnCount()):
            header.setSectionResizeMode(idx, QHeaderView.ResizeMode.Interactive)
        table.resizeColumnsToContents()
        header.resizeSection(stretch_column, max(header.sectionSize(stretch_column), 300))

    @staticmethod
    def _readonly_item(value: object = "") -> QTableWidgetItem:
        item = QTableWidgetItem(str(value))
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    @staticmethod
    def _make_label_selectable(label: QLabel) -> None:
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )

    def restore_table_layout(self, name: str, table: QTableWidget) -> None:
        state = self.settings.table_layout(name)
        if state:
            table.horizontalHeader().restoreState(QByteArray(state))

    def save_table_layout(self, name: str, table: QTableWidget) -> None:
        self.settings.set_table_layout(name, bytes(table.horizontalHeader().saveState()))

    def configure_column_menu(self) -> None:
        self.column_menu.clear()
        for column in range(self.table.columnCount()):
            label = str(
                self.table.model().headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) or ""
            )
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(not self.table.isColumnHidden(column))
            action.triggered.connect(lambda checked=False, col=column: self.toggle_column_visibility(col, bool(checked)))
            self.column_menu.addAction(action)

    def toggle_column_visibility(self, column: int, visible: bool) -> None:
        if column == SOUND_COL and not visible:
            self.statusBar().showMessage("Sound column stays visible", 2500)
            return
        self.table.horizontalHeader().setSectionHidden(column, not visible)
        self.settings.set_hidden_table_columns("library", self.hidden_library_columns())
        self.configure_column_menu()

    def hidden_library_columns(self) -> list[int]:
        return [column for column in range(self.table.columnCount()) if self.table.isColumnHidden(column)]

    def restore_hidden_columns(self) -> None:
        saved = self.settings.hidden_table_columns("library")
        if saved:
            columns_to_hide: list[int] = saved
        elif self.settings.table_layout("library") is None:
            columns_to_hide = list(DEFAULT_HIDDEN_LIBRARY_COLUMNS)
        else:
            columns_to_hide = []
        for column in columns_to_hide:
            if 0 <= column < self.table.columnCount() and column != SOUND_COL:
                self.table.horizontalHeader().setSectionHidden(column, True)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._flush_user_notes()
        self._stop_external_audio()
        for timer_name in (
            "_relay_poll_timer", "_index_timer", "_worker_timer",
            "_import_progress_timer", "_transcript_refresh_timer",
        ):
            timer = getattr(self, timer_name, None)
            if timer is not None:
                timer.stop()
        # Cooperatively cancel the background workers BEFORE waiting. Each run loop
        # polls QThread.isInterruptionRequested between items and forwards it into
        # the active capture subprocess / whisper segment loop (both killable
        # mid-flight), so a quit during a long bulk import or transcription returns in
        # well under a second instead of timing out. Waiting without first requesting
        # interruption is what let a still-running QThread be destroyed at teardown and
        # crash Qt with SIGABRT.
        workers = [
            getattr(self, name, None)
            for name in ("_poll_worker", "_import_worker", "_transcribe_worker", "_transcribe_queue_worker")
        ]
        for worker in workers:
            if worker is not None and worker.isRunning():
                worker.requestInterruption()
        for worker in workers:
            if worker is not None and worker.isRunning():
                worker.wait(10000)
        self.save_table_layout("library", self.table)
        self.save_table_layout("inbox_v2", self.inbox_table)
        self.settings.set_hidden_table_columns("library", self.hidden_library_columns())
        self.save_library_search_state()
        super().closeEvent(event)

    def _set_combo_by_data(self, combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def restore_library_search_state(self) -> None:
        state = self.settings.library_search_state()
        self.search_box.setText(str(state.get("query") or ""))
        self._set_combo_by_data(self.duration_filter, str(state.get("duration_filter") or "all"))
        self._set_combo_by_data(self.media_filter, str(state.get("media_filter") or "all"))
        self._set_combo_by_data(self.status_filter, str(state.get("status_filter") or "all"))
        self._set_combo_by_data(self.usage_filter, str(state.get("usage_filter") or "all"))
        self.active_library_filter = str(state.get("library_filter") or "all")
        valid_filters = {"all", "favorites", "smart:needs_transcript", "smart:instrumental", "smart:missing_audio", "smart:high_popularity", "smart:has_videos"}
        valid_filters.update(f"bin:{bin_row.id}" for bin_row in self.vm.library_bins())
        if self.active_library_filter not in valid_filters:
            self.active_library_filter = "all"
        self._sync_library_sidebar_active_state()
        self._pending_selected_music_id = str(state.get("selected_music_id") or "") or None

    def save_library_search_state(self) -> None:
        self.settings.set_library_search_state(
            {
                "query": self.search_box.text(),
                "duration_filter": str(self.duration_filter.currentData() or "all"),
                "media_filter": str(self.media_filter.currentData() or "all"),
                "status_filter": str(self.status_filter.currentData() or "all"),
                "usage_filter": str(self.usage_filter.currentData() or "all"),
                "library_filter": self.active_library_filter,
                "selected_music_id": self._selected_music_id() or "",
            }
        )

    def show_view(self, name: str) -> None:
        if name == "settings":
            self.open_settings_dialog()
            return
        mapping = {
            "library": (self.library_view, "Library"),
            "inbox": (self.inbox_view, "Shortcut inbox"),
            "review": (self.review_view, "Review queues"),
            "dedupe": (self.dedupe_view, "Duplicate review"),
            "worker": (self.worker_view, "Worker status"),
        }
        widget, title = mapping.get(name, mapping["library"])
        self.stack.setCurrentWidget(widget)
        self.title_label.setText(title)
        for key, button in self.nav_buttons.items():
            button.setProperty("active", key == name)
            button.style().unpolish(button)
            button.style().polish(button)
        if name == "inbox":
            self.refresh_inbox()
        elif name == "review":
            self.refresh_review_queues()
        elif name == "dedupe":
            self.refresh_dedupe_review()
        elif name == "worker":
            self.refresh_worker_status()
        elif name == "library" and getattr(self, "_library_needs_transcript_refresh", False):
            # Surface transcripts that finished while the library tab was hidden.
            self._library_needs_transcript_refresh = False
            self.refresh_table()

    def choose_vault(self) -> None:
        self.open_vault()

    def _vault_dialog_start_dir(self) -> str:
        """A FAST local directory to open file pickers in. Never point the native
        macOS picker at a network mount (e.g. /Volumes/…) — enumerating a slow or
        stale share there beach-balls the whole app before the dialog even shows."""
        from sound_vault.settings import default_vault_root

        root = self.vault_root
        if str(root).startswith("/Volumes/"):
            return str(default_vault_root().parent)  # local ~/Documents
        try:
            return str(root if root.exists() else default_vault_root().parent)
        except OSError:
            return str(Path.home())

    def open_vault(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Open a Sound Cache vault", self._vault_dialog_start_dir()
        )
        if selected:
            self._switch_to_vault(Path(selected))

    def new_vault(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Choose or create an empty folder for the new vault", self._vault_dialog_start_dir()
        )
        if not selected:
            return
        target = Path(selected)
        # If the chosen folder already holds a vault, treat it as opening that one.
        if (target / "catalog").exists() or any(target.glob("sounds/*")):
            resp = QMessageBox.question(
                self, "Folder already has sounds",
                f"“{target.name}” already contains a Sound Cache vault.\n\nOpen it instead?",
                QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Cancel,
            )
            if resp != QMessageBox.StandardButton.Open:
                return
        self._switch_to_vault(target)

    def open_recent_vault(self, path: str) -> None:
        target = Path(path)
        if not target.exists():
            QMessageBox.warning(self, "Vault not found", f"That vault no longer exists:\n{path}")
            return
        self._switch_to_vault(target)

    def _switch_to_vault(self, path: Path) -> None:
        """Point the app at a vault directory: persist it, rebind the view model
        (tearing down the old executor), refresh the UI, and rebuild the index."""
        self.vault_root = resolve_vault_root(Path(path))
        self.settings.set_vault_root(self.vault_root)
        self.settings.add_recent_vault(self.vault_root)
        if hasattr(self, "vault_label"):
            self.vault_label.setText(str(self.vault_root))
        # Tear down the previous view model's executor before rebinding so its
        # worker thread doesn't leak across vault switches.
        old_vm = getattr(self, "vm", None)
        if old_vm is not None:
            old_vm.close()
        # The previous vault's index rebuild may still be running on its (now
        # shut-down) executor. Drop our handle so (a) rebuild_index() doesn't
        # early-return "already running" and skip the new vault, and (b) the stale
        # completion callback for the old vault is ignored against the new model.
        self._index_future = None
        self._index_timer.stop()
        self.vm = LibraryViewModel(
            vault_root=self.vault_root,
            index_path=index_path_for_vault(self.vault_root),
            load_sidecars=False,
            sidecar_mode="summary",
        )
        self.setWindowTitle(f"Sound Cache — {self.vault_root.name}")
        if hasattr(self, "recent_menu"):
            self._rebuild_recent_menu()
        self.refresh_library_sidebar()
        self.reset_library_filters()
        # Defer the rebuild a tick so the switch + repaint complete first (keeps the
        # window responsive instead of appearing to hang on a large vault).
        QTimer.singleShot(0, self.rebuild_index)
        write_event("gui.vault_switched", vault_root=str(self.vault_root))

    # ----- application menu bar -------------------------------------------------
    def _add_action(self, menu, label: str, slot, shortcut: str | None = None, *, role=None):
        action = QAction(label, self)
        action.triggered.connect(lambda _checked=False: slot())
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        if role is not None:
            action.setMenuRole(role)
        menu.addAction(action)
        return action

    def _build_menu_bar(self) -> None:
        bar = self.menuBar()
        bar.clear()

        file_menu = bar.addMenu("&File")
        self._add_action(file_menu, "New Vault…", self.new_vault, "Ctrl+Shift+N")
        self._add_action(file_menu, "Open Vault…", self.open_vault, "Ctrl+O")
        self.recent_menu = file_menu.addMenu("Open Recent")
        self._rebuild_recent_menu()
        file_menu.addSeparator()
        self._add_action(file_menu, "Import Sound Pack…", self.import_sound_pack)
        self._add_action(file_menu, "Import TikTok Export…", self.import_tiktok_favorite_sounds_export)
        file_menu.addSeparator()
        self._add_action(file_menu, "Reveal Vault in Finder", self.reveal_vault_in_finder, "Ctrl+Shift+R")
        self._add_action(file_menu, "Open App Data Folder", self.open_data_folder)
        file_menu.addSeparator()
        self._add_action(file_menu, "Settings…", lambda: self.show_view("settings"), "Ctrl+,",
                         role=QAction.MenuRole.PreferencesRole)
        self._add_action(file_menu, "Quit Sound Cache", self.close, "Ctrl+Q", role=QAction.MenuRole.QuitRole)

        edit_menu = bar.addMenu("&Edit")
        self._add_action(edit_menu, "Undo", lambda: self._edit_route("undo"), "Ctrl+Z")
        self._add_action(edit_menu, "Redo", lambda: self._edit_route("redo"), "Ctrl+Shift+Z")
        edit_menu.addSeparator()
        self._add_action(edit_menu, "Cut", lambda: self._edit_route("cut"), "Ctrl+X")
        self._add_action(edit_menu, "Copy", lambda: self._edit_route("copy"), "Ctrl+C")
        self._add_action(edit_menu, "Paste", lambda: self._edit_route("paste"), "Ctrl+V")
        self._add_action(edit_menu, "Select All", lambda: self._edit_route("selectAll"), "Ctrl+A")
        edit_menu.addSeparator()
        self._add_action(edit_menu, "Find / Search", self.focus_search, "Ctrl+F")

        view_menu = bar.addMenu("&View")
        self._add_action(view_menu, "Library", lambda: self.show_view("library"), "Ctrl+1")
        self._add_action(view_menu, "Shortcut Inbox", lambda: self.show_view("inbox"), "Ctrl+2")
        self._add_action(view_menu, "Review Queues", lambda: self.show_view("review"), "Ctrl+3")
        self._add_action(view_menu, "Duplicate Review", lambda: self.show_view("dedupe"), "Ctrl+4")
        self._add_action(view_menu, "Worker Status", lambda: self.show_view("worker"), "Ctrl+5")
        view_menu.addSeparator()
        filter_menu = view_menu.addMenu("Filter Library")
        for label, value in (
            ("All sounds", "all"),
            ("★ Favorites", "favorites"),
            ("Not transcribed yet", "smart:needs_transcript"),
            ("Missing audio", "smart:missing_audio"),
            ("100K+ uses", "smart:high_popularity"),
            ("Has example videos", "smart:has_videos"),
        ):
            self._add_action(filter_menu, label, lambda v=value: self.apply_library_filter(v))
        view_menu.addSeparator()
        self._add_action(view_menu, "Rebuild Index", self.rebuild_index, "Ctrl+R")

        vault_menu = bar.addMenu("&Vault")
        self._add_action(vault_menu, "Connect TikTok…", self.open_tiktok_connect)
        self._add_action(vault_menu, "Rebuild Index", self.rebuild_index)
        self._add_action(vault_menu, "Transcribe Pending Sounds", lambda: self._start_backlog_transcription(force=True))
        self._add_action(vault_menu, "Vault Info…", self.show_vault_info)
        vault_menu.addSeparator()
        self._add_action(vault_menu, "Repair Names for Portability…", self.repair_folder_portability)
        self._add_action(vault_menu, "Setup Wizard…", self.run_onboarding)

        help_menu = bar.addMenu("&Help")
        self._add_action(help_menu, "Welcome / Setup Wizard…", self.run_onboarding)
        self._add_action(help_menu, "Open App Data Folder", self.open_data_folder)
        self._add_action(help_menu, "Open soundcache.io", lambda: self._open_url("https://soundcache.io"))
        self._add_action(help_menu, "About Sound Cache", self.show_about, role=QAction.MenuRole.AboutRole)

    def repair_folder_portability(self) -> None:
        """Make every sound folder/audio name safe to copy to NFS/ext4/SMB/FAT.

        Runs a read-only scan first and only renames after the user confirms (this
        rewrites their on-disk vault). The music-id prefix is always preserved, so
        the indexer still finds every sound by its glob even before the reindex."""
        from sound_vault.workers.folder_portability import repair_folder_portability as _repair

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            preview = _repair(self.vault_root, dry_run=True)
        except OSError as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Repair Names for Portability", f"Could not scan the vault:\n{exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        if preview.folders_renamed == 0 and preview.audio_renamed == 0:
            QMessageBox.information(
                self, "Repair Names for Portability",
                f"Scanned {preview.scanned} sound folder(s). All names are already "
                "filesystem-portable — nothing to repair. ✅",
            )
            return

        detail = "\n".join(
            f"• {r.old_folder}\n    → {r.new_folder}" for r in preview.repairs[:12] if r.folder_changed
        )
        if len(preview.repairs) > 12:
            detail += f"\n…and {len(preview.repairs) - 12} more"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Repair Names for Portability")
        box.setText(
            f"{preview.folders_renamed} folder name(s) and {preview.audio_renamed} audio file(s) "
            "contain characters that work on this Mac but can break when the vault is copied "
            "to a network drive (NFS/SMB) or another filesystem.\n\n"
            "Rename them to portable equivalents? The music-id prefix is preserved, so your "
            "sounds stay intact and findable."
        )
        if detail:
            box.setDetailedText(detail)
        apply_btn = box.addButton("Rename", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not apply_btn:
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = _repair(self.vault_root, dry_run=False)
        except OSError as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Repair Names for Portability", f"Repair failed partway:\n{exc}")
            self.rebuild_index()
            return
        finally:
            QApplication.restoreOverrideCursor()
        write_event(
            "gui.folder_portability_repaired",
            folders=str(result.folders_renamed), audio=str(result.audio_renamed),
        )
        self.rebuild_index()  # stored paths changed; refresh the index from disk
        self.statusBar().showMessage(
            f"Renamed {result.folders_renamed} folder(s) + {result.audio_renamed} audio file(s) "
            "for portability.", 8000,
        )

    def _rebuild_recent_menu(self) -> None:
        menu = getattr(self, "recent_menu", None)
        if menu is None:
            return
        menu.clear()
        recents = [p for p in self.settings.recent_vaults() if p != str(self.vault_root)]
        if not recents:
            empty = menu.addAction("No recent vaults")
            empty.setEnabled(False)
            return
        for path in recents:
            self._add_action(menu, path, lambda p=path: self.open_recent_vault(p))

    def _edit_route(self, method: str) -> None:
        """Route standard Edit actions to the focused widget (text fields, notes,
        table) — the same widgets handle these natively, so the menu just exposes them."""
        widget = QApplication.focusWidget()
        handler = getattr(widget, method, None)
        if callable(handler):
            handler()

    def reveal_vault_in_finder(self) -> None:
        if not self._vault_available():
            QMessageBox.warning(self, "Vault offline", "The vault folder isn't reachable right now.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.vault_root)))

    def open_data_folder(self) -> None:
        from sound_vault.settings import user_data_dir

        data_dir = user_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_dir)))

    def _open_url(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    def show_vault_info(self) -> None:
        try:
            stats = self.vm.stats_text()
        except Exception:  # noqa: BLE001 - display-only
            stats = "unavailable"
        QMessageBox.information(
            self,
            "Vault info",
            "\n".join(
                [
                    f"Vault: {self.vault_root}",
                    f"Reachable: {'yes' if self._vault_available() else 'no (offline)'}",
                    f"Index: {index_path_for_vault(self.vault_root)}",
                    f"Inbox queue: {inbox_path_for_vault(self.vault_root)}",
                    "",
                    stats,
                ]
            ),
        )

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Sound Cache",
            "<b>Sound Cache</b><br>A local-first vault for the sounds you save.<br><br>"
            "No login, no cloud — just a folder that's yours.<br>"
            "<a href='https://soundcache.io'>soundcache.io</a>",
        )

    def run_onboarding(self) -> None:
        dialog = OnboardingDialog(
            default_vault=self.vault_root,
            connect_tiktok=self.open_tiktok_connect,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            chosen = dialog.chosen_vault()
            if chosen and chosen != self.vault_root:
                self._switch_to_vault(chosen)
        self.settings.set_onboarding_complete(True)

    def _maybe_run_onboarding(self) -> None:
        """Show the setup wizard once on a brand-new install (no vault configured).
        Returning users (who already have a vault set) skip straight in."""
        if _env_flag("SOUND_VAULT_DISABLE_ONBOARDING"):
            return
        if self.settings.onboarding_complete():
            return
        if self.settings.vault_root_is_set():
            self.settings.set_onboarding_complete(True)  # existing user — don't nag
            return
        QTimer.singleShot(300, self.run_onboarding)

    def _vault_available(self) -> bool:
        """True if the vault root is reachable. Guarded so a stale/disconnected
        network mount (which can raise OSError on stat) reads as 'unavailable'
        rather than crashing."""
        try:
            return self.vault_root.exists() and self.vault_root.is_dir()
        except OSError:
            return False

    def rebuild_index(self) -> None:
        if self._index_future is not None and not self._index_future.done():
            self.statusBar().showMessage("Index rebuild already running", 2500)
            return
        # If the vault drive is offline, do NOT rebuild — that would wipe the local
        # index cache and make it look like the library vanished. Keep showing the
        # last-known library from the cache and tell the user the drive is offline.
        if not self._vault_available():
            write_event("gui.vault_unavailable", vault_root=str(self.vault_root))
            self._set_index_status("VAULT OFFLINE", state="error")
            self.worker_label.setText("Worker\nvault offline")
            self.statusBar().showMessage(
                "Vault drive unavailable — showing your last-known library. "
                "Your sounds are safe; reconnect the drive and Rebuild index to refresh.",
                12000,
            )
            try:
                self.stats_label.setText(self.vm.stats_text())  # cached count (local SQLite)
            except Exception:  # noqa: BLE001 - display-only
                pass
            self.refresh_table()  # render whatever the local cache still holds
            return
        write_event(
            "gui.index_rebuild_requested",
            vault_root=str(self.vault_root),
            index_path=str(index_path_for_vault(self.vault_root)),
        )
        self.worker_label.setText("Worker\nindexing…")
        self._set_index_status("INDEXING", state="running")
        self.rebuild_button.setEnabled(False)
        self._index_future = self.vm.rebuild_index_async()
        self._index_timer.start()

    def _finish_async_index(self) -> None:
        if self._index_future is None or not self._index_future.done():
            return
        self._index_timer.stop()
        try:
            count = self._index_future.result()
            self.stats_label.setText(self.vm.stats_text())
            self.catalog_label.setText(self.vm.catalog_stats_text())
            self.worker_label.setText(f"Worker\nidle • indexed {count:,}")
            self._set_index_status(f"INDEXED {count:,}", state="online")
            self.refresh_table()
            self.refresh_portal_tabs()
            self.refresh_inbox()
            self.refresh_review_queues()
            self.refresh_worker_status()
            write_event("gui.index_rebuild_complete", vault_root=str(self.vault_root), records=count)
            # Kick off a one-time background sweep that transcribes the existing
            # backlog (sounds with audio but no transcript). New imports already
            # transcribe via the live pipeline; this catches everything that landed
            # before transcription was wired up (or in an older build).
            QTimer.singleShot(1500, self._start_backlog_transcription)
        except Exception as exc:
            self.worker_label.setText(f"Worker\nindex error: {exc}")
            self.stats_label.setText("index failed")
            self._set_index_status("INDEX ERROR", state="error")
            write_event("gui.index_rebuild_exception", **exception_fields(exc))
        finally:
            self.rebuild_button.setEnabled(True)
            self._index_future = None

    def schedule_refresh_table(self) -> None:
        self._search_timer.start()

    def refresh_table(self) -> None:
        search_had_focus = self.search_box.hasFocus()
        search_cursor_position = self.search_box.cursorPosition()
        selected_music_id = self._selected_music_id() or self._pending_selected_music_id
        self._pending_selected_music_id = None
        scroll_value = self.table.verticalScrollBar().value()
        duration_filter = self.duration_filter.currentData() if hasattr(self, "duration_filter") else "all"
        media_filter = self.media_filter.currentData() if hasattr(self, "media_filter") else "all"
        status_filter = self.status_filter.currentData() if hasattr(self, "status_filter") else "all"
        usage_filter = self.usage_filter.currentData() if hasattr(self, "usage_filter") else "all"
        self.current_rows = self.vm.search(
            self.search_box.text(),
            duration_filter=str(duration_filter or "all"),
            media_filter=str(media_filter or "all"),
            status_filter=str(status_filter or "all"),
            usage_filter=str(usage_filter or "all"),
        )
        self.current_rows = self._apply_library_collection_filter(self.current_rows)
        self._sort_library_rows()
        self.records_by_id = {record.music_id: record for record in self.current_rows}
        total = self.vm.db.stats().total_sounds
        active_filters = self._active_library_filter_summary(
            duration_filter=str(duration_filter or "all"),
            media_filter=str(media_filter or "all"),
            status_filter=str(status_filter or "all"),
            usage_filter=str(usage_filter or "all"),
        )
        query_note = " • query active" if self.search_box.text().strip() else ""
        suffix = f" • {active_filters}" if active_filters else ""
        self.result_count_label.setText(f"{len(self.current_rows):,} displayed / {total:,} indexed{suffix}{query_note}")
        # Model reset — O(1) per cell on demand instead of building 11×N
        # QTableWidgetItems on every keystroke/filter/sort (the old beach-ball).
        favorites_set = set(self.vm.favorite_music_ids())
        playable_set = {r.music_id for r in self.current_rows if self._record_has_playable_pointer(r)}
        self.table.library_model().set_rows(self.current_rows, favorites=favorites_set, playable=playable_set)
        self.table.horizontalHeader().setSortIndicator(self.library_sort_column, self.library_sort_order)
        if self.current_rows:
            self._restore_library_selection(selected_music_id, scroll_value)
        else:
            self.clear_preview("No matching sounds")
        if search_had_focus:
            self.search_box.setFocus(Qt.FocusReason.OtherFocusReason)
            self.search_box.setCursorPosition(min(search_cursor_position, len(self.search_box.text())))

    @staticmethod
    def _active_library_filter_summary(
        *,
        duration_filter: str,
        media_filter: str,
        status_filter: str,
        usage_filter: str,
    ) -> str:
        labels = []
        for label, value in (
            ("duration", duration_filter),
            ("media", media_filter),
            ("status", status_filter),
            ("popularity", usage_filter),
        ):
            if value and value != "all":
                labels.append(f"{label}: {value.replace('_', ' ')}")
        return " • ".join(labels)

    def _apply_library_collection_filter(self, rows: list) -> list:
        value = self.active_library_filter
        if value == "favorites":
            favorites = set(self.vm.favorite_music_ids())
            return [record for record in rows if record.music_id in favorites]
        if value.startswith("bin:"):
            ids = set(self.vm.library_bin_music_ids(value.removeprefix("bin:")))
            return [record for record in rows if record.music_id in ids]
        if value == "smart:needs_transcript":
            return [record for record in rows if transcript_state(record) == "pending"]
        if value == "smart:instrumental":
            return [record for record in rows if transcript_state(record) == "empty"]
        if value == "smart:missing_audio":
            return [record for record in rows if not self._record_has_playable_pointer(record)]
        if value == "smart:high_popularity":
            return [record for record in rows if (record.usage_count or 0) >= 100_000]
        if value == "smart:has_videos":
            return [record for record in rows if record.associated_video_count > 0]
        return rows

    def _library_filter_label(self, value: str) -> str:
        if value == "favorites":
            return "Showing Favorites"
        if value.startswith("bin:"):
            bin_id = value.removeprefix("bin:")
            for bin_row in self.vm.library_bins():
                if bin_row.id == bin_id:
                    return f"Showing sorting bin: {bin_row.name}"
        if value.startswith("smart:"):
            return f"Smart sort: {value.removeprefix('smart:').replace('_', ' ')}"
        return "Showing full Library"

    def handle_library_header_clicked(self, column: int) -> None:
        if column == self.library_sort_column:
            self.library_sort_order = (
                Qt.SortOrder.AscendingOrder
                if self.library_sort_order == Qt.SortOrder.DescendingOrder
                else Qt.SortOrder.DescendingOrder
            )
        else:
            self.library_sort_column = column
            self.library_sort_order = Qt.SortOrder.DescendingOrder if column == POPULARITY_COL else Qt.SortOrder.AscendingOrder
        self.table.horizontalHeader().setSortIndicator(self.library_sort_column, self.library_sort_order)
        self.statusBar().showMessage("Sorting…", 800)
        QTimer.singleShot(0, self.refresh_table)

    def _sort_library_rows(self) -> None:
        reverse = self.library_sort_order == Qt.SortOrder.DescendingOrder
        favorites_set = set(self.vm.favorite_music_ids()) if self.library_sort_column == 0 else frozenset()

        def sort_key(record):
            match self.library_sort_column:
                case 0:
                    return 1 if record.music_id in favorites_set else 0
                case 1:
                    return 1 if self._record_has_playable_pointer(record) else 0
                case 2:
                    return (record.title or record.music_id or "").lower()
                case 3:
                    return (record.artist or "").lower()
                case 4:
                    return (record.status or "").lower()
                case 5:
                    return record.added_at or ""
                case 6:
                    return record.packaged_at or ""
                case 7:
                    return record.usage_count if record.usage_count is not None else -1
                case 8:
                    return record.associated_video_count
                case 9:
                    return 1 if self._record_has_playable_pointer(record) else 0
                case 10:
                    return (", ".join(record.tags[:3]) or record.status or "").lower()
                case _:
                    return (record.title or record.music_id or "").lower()

        self.current_rows.sort(key=lambda record: (sort_key(record), record.music_id), reverse=reverse)

    @staticmethod
    def _record_has_playable_pointer(record) -> bool:
        if record.local_audio_path is not None:
            return True
        if not isinstance(record.raw, dict):
            return False
        for key in ("preview_url", "audio_url", "media_url"):
            value = record.raw.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return True
        paths = record.raw.get("paths")
        if not isinstance(paths, dict):
            return False
        return any(paths.get(key) for key in ("audio", "preview", "preview_audio", "m4a", "file"))

    def _selected_music_id(self) -> str | None:
        selected_rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() is not None else []
        selected_row_numbers = {index.row() for index in selected_rows}
        current_row = self.table.currentRow()
        if current_row in selected_row_numbers:
            return self._library_music_id_for_row(current_row)
        selected = self._selected_library_music_ids()
        return selected[0] if selected else None

    def _selected_library_music_ids(self) -> tuple[str, ...]:
        selection_model = self.table.selectionModel()
        rows = selection_model.selectedRows() if selection_model is not None else []
        music_ids = []
        for index in sorted(rows, key=lambda item: item.row()):
            music_id = self._library_music_id_for_row(index.row())
            if music_id:
                music_ids.append(music_id)
        return self._dedupe_music_ids(music_ids)

    def _library_music_id_for_row(self, row: int) -> str | None:
        if row < 0:
            return None
        value = self.table.music_id_at(row)
        return str(value) if value else None

    @staticmethod
    def _dedupe_music_ids(music_ids: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        out = []
        seen: set[str] = set()
        for music_id in music_ids:
            value = str(music_id or "").strip()
            if value and value not in seen:
                seen.add(value)
                out.append(value)
        return tuple(out)

    def _restore_library_selection(self, selected_music_id: str | None, scroll_value: int) -> None:
        target_row = 0
        if selected_music_id:
            for row_idx in range(self.table.rowCount()):
                if self.table.music_id_at(row_idx) == selected_music_id:
                    target_row = row_idx
                    break
        self.table.clearSelection()
        self.table.selectRow(target_row)
        self.table.verticalScrollBar().setValue(scroll_value)
        self.update_preview_from_selection()

    def clear_preview(self, title: str = "Select a sound") -> None:
        self._flush_user_notes()
        self._notes_music_id = None
        if hasattr(self, "user_notes_edit"):
            self._loading_notes = True
            self.user_notes_edit.clear()
            self._loading_notes = False
        self.current_preview_record = None
        self.preview_title.setText(title)
        if hasattr(self, "now_playing_title"):
            self.now_playing_title.setText("select a sound to inspect waveform, provenance, transcript, and evidence")
        self.preview_meta.setText("Audio, tags, evidence and local paths will show here.")
        self.preview_tags.setText("")
        if hasattr(self, "transcript_text"):
            self.transcript_text.clear()
            self.transcript_text.setPlaceholderText(self._DEFAULT_TRANSCRIPT_PLACEHOLDER)
        self.artwork_label.setPixmap(QPixmap())
        self.artwork_label.setText("no artwork")
        self.evidence_list.setText("No local screenshots yet.")
        self.video_table.setRowCount(0)
        self.open_video.setEnabled(False)
        self.open_video_url.setEnabled(False)
        self.open_folder.setEnabled(False)
        self.open_tiktok_sound.setEnabled(False)
        self.open_spotify.setVisible(False)
        self.copy_metadata.setEnabled(False)
        self.progress_slider.setRange(0, 0)
        self.time_label.setText("0:00 / 0:00")
        self.playback_status.setText("Playback idle")

    def update_preview_from_selection(self) -> None:
        selected_ids = self._selected_library_music_ids()
        if not selected_ids:
            self.clear_preview()
            return
        selection_model = self.table.selectionModel()
        selected_rows = selection_model.selectedRows() if selection_model is not None else []
        selected_row_numbers = {index.row() for index in selected_rows}
        row = self.table.currentRow()
        if row < 0 or row not in selected_row_numbers:
            row = min(selected_row_numbers) if selected_row_numbers else 0
        music_id = self.table.music_id_at(row)
        if not music_id:
            self.clear_preview()
            return
        row_record = self.records_by_id.get(str(music_id))
        if row_record is None:
            self.clear_preview()
            return
        self._show_preview_record(row_record)
        self._preview_token += 1
        token = self._preview_token
        music_id_str = row_record.music_id
        self._preview_executor.submit(self._hydrate_preview_in_background, token, music_id_str)

    def _hydrate_preview_in_background(self, token: int, music_id: str) -> None:
        try:
            record = self.vm.preview_for(music_id)
        except Exception as exc:
            write_event("gui.preview_hydrate_exception", **exception_fields(exc))
            return
        self.previewHydrated.emit(token, music_id, record)

    def _apply_hydrated_preview(self, token: int, music_id: str, record) -> None:
        if token != self._preview_token:
            return
        if self.current_preview_record is None or self.current_preview_record.music_id != music_id:
            return
        self._show_preview_record(record)

    def _show_preview_record(self, record) -> None:
        self.current_preview_record = record
        self.preview_title.setText(record.title or record.music_id)
        if hasattr(self, "now_playing_title"):
            full = f"{record.title or record.music_id} · {record.artist or 'unknown source'}"
            if len(full) > 48:
                full = full[:45] + "…"
            self.now_playing_title.setText(full)
        self.preview_meta.setText(self._formatted_metadata(record))
        self.preview_tags.setText(" ".join(f"#{tag}" for tag in record.tags) or "no tags yet")
        # Set the state-specific placeholder first, so an empty transcript box
        # explains *why* (no speech / no audio / not run yet) rather than implying
        # a lookup failure.
        self.transcript_text.setPlaceholderText(self._transcript_placeholder(record))
        self.transcript_text.setPlainText(self._full_transcript_text(record))
        self._load_user_notes(record)
        self._populate_artwork(record)
        self._populate_evidence(record)
        self._populate_videos(record)
        self.open_folder.setEnabled(record.folder_path is not None)
        self.open_tiktok_sound.setEnabled(self._sound_url_for_record(record) is not None)
        self.open_spotify.setVisible(self._spotify_url_for_record(record) is not None)
        self.copy_metadata.setEnabled(True)
        target = self.vm.play_target_for(record)
        duration_ms = self._duration_ms_for_record(record)
        self.progress_slider.setRange(0, duration_ms)
        self.progress_slider.setValue(0)
        self.time_label.setText(f"0:00 / {self._format_ms(duration_ms)}")
        self.playback_status.setText("Ready to play" if target is not None else "No playable audio source")

    # ---- user notes (editable, autosaved, searchable) ----
    def _on_user_notes_changed(self) -> None:
        if self._loading_notes:
            return
        self._notes_save_timer.start()

    def _load_user_notes(self, record) -> None:
        """Load a sound's notes into the editor; only resets text when the sound
        actually changes (so an in-flight edit isn't clobbered by hydration)."""
        mid = str(record.music_id)
        if mid == self._notes_music_id:
            return
        self._flush_user_notes()  # persist the previous sound's edits first
        self._notes_music_id = mid
        self._loading_notes = True
        self.user_notes_edit.setPlainText(getattr(record, "user_notes", "") or "")
        self._loading_notes = False

    def _flush_user_notes(self) -> None:
        self._notes_save_timer.stop()
        mid = self._notes_music_id
        if not mid:
            return
        text = self.user_notes_edit.toPlainText()
        record = self.records_by_id.get(str(mid))
        if record is not None and (getattr(record, "user_notes", "") or "") == text:
            return  # unchanged, nothing to write
        if self.vm.set_user_notes(mid, text):
            from dataclasses import replace

            if record is not None:
                try:
                    self.records_by_id[str(mid)] = replace(record, user_notes=text)
                except Exception:  # noqa: BLE001
                    pass
            if self.current_preview_record is not None and str(self.current_preview_record.music_id) == str(mid):
                try:
                    self.current_preview_record = replace(self.current_preview_record, user_notes=text)
                except Exception:  # noqa: BLE001
                    pass
            self.statusBar().showMessage("Notes saved", 1500)

    def _formatted_metadata(self, record) -> str:
        parts = [
            f"artist/source: {record.artist or 'pending'}",
            f"music id: {record.music_id}",
            f"status: {record.status}",
            f"added: {record.added_at or 'unknown'}",
            f"packaged: {record.packaged_at or 'not packaged'}",
            f"videos: {record.associated_video_count}",
            f"local audio: {record.local_audio_path.name if record.local_audio_path else 'missing'}",
            f"artwork: {record.artwork_path.name if record.artwork_path else 'missing true sound art'}",
            f"usage count: {record.usage_count:,}" if record.usage_count is not None else "usage count: unknown",
            f"source provider: {record.source_provider or 'unknown'}",
            f"source confidence: {record.source_confidence or 'unknown'}",
            f"vault version: {record.vault_version or 'unknown'}",
            f"canonical url: {record.canonical_url or 'missing'}",
            f"source music: {record.source_music_url or 'missing'}",
            f"duration: {self._format_seconds(record.duration_seconds) if record.duration_seconds is not None else 'unknown'}",
            f"hashtags: {' '.join(f'#{tag}' for tag in record.hashtags) if getattr(record, 'hashtags', None) else 'none captured'}",
            self._format_transcript_status(record),
            f"music page: {record.music_page_title or 'unknown'}",
            f"captured: {record.video_manifest_captured_at or 'unknown'}",
        ]
        return "\n".join(parts)

    # Canonical 4-state transcript classifier lives in the indexer so filters,
    # health metrics, and this inspector all agree. See indexer.transcript_state.
    _transcript_state = staticmethod(transcript_state)

    @classmethod
    def _format_transcript_status(cls, record) -> str:
        state = cls._transcript_state(record)
        if state == "available":
            source = f" • {record.transcript_path.name}" if record.transcript_path else ""
            language = f" • {record.transcript_language}" if record.transcript_language else ""
            return f"transcript: available ({len(record.transcript_text):,} chars{language}{source})"
        return {
            "empty": "transcript: empty (no speech detected — likely instrumental)",
            "no_audio": "transcript: no audio to transcribe",
            "pending": "transcript: not run yet",
        }[state]

    # Placeholder shown in the (empty) transcript box, matched to the state above.
    _TRANSCRIPT_PLACEHOLDERS = {
        "empty": "Transcript is empty — no speech detected (likely an instrumental).",
        "no_audio": "No audio is available for this sound, so there's nothing to transcribe.",
        "pending": "Not transcribed yet — run transcription from Worker status, or Re-enrich this sound.",
    }

    @classmethod
    def _transcript_placeholder(cls, record) -> str:
        return cls._TRANSCRIPT_PLACEHOLDERS.get(cls._transcript_state(record), cls._DEFAULT_TRANSCRIPT_PLACEHOLDER)

    _DEFAULT_TRANSCRIPT_PLACEHOLDER = "Select a sound to view its transcript."

    @staticmethod
    def _full_transcript_text(record) -> str:
        return record.transcript_text or ""

    def _populate_artwork(self, record) -> None:
        image = record.artwork_path or (record.evidence_images[0] if record.evidence_images else None)
        if image is None:
            self.artwork_label.setPixmap(QPixmap())
            self.artwork_label.setText("no artwork")
            return
        pixmap = QPixmap(str(image))
        if pixmap.isNull():
            self.artwork_label.setText(image.name)
            return
        self.artwork_label.setText("")
        self.artwork_label.setPixmap(
            pixmap.scaled(
                self.artwork_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _populate_evidence(self, record) -> None:
        lines = [f"{idx}. {path.name}" for idx, path in enumerate(record.evidence_images[:8], start=1)]
        if record.local_audio_path:
            lines.insert(0, f"audio: {record.local_audio_path.name}")
        self.evidence_list.setText("\n".join(lines) if lines else "No local screenshots yet.")

    def _populate_videos(self, record) -> None:
        self.video_table.setSortingEnabled(False)
        self.video_table.setRowCount(len(record.associated_videos))
        for row_idx, video in enumerate(record.associated_videos):
            notes = video.description
            if getattr(video, "hashtags", None):
                tag_line = " ".join(f"#{tag}" for tag in video.hashtags)
                notes = f"{notes}\n{tag_line}" if notes else tag_line
            if video.page_title:
                notes = f"{video.page_title}\n{notes}" if notes else video.page_title
            if video.captured_at:
                notes = f"{notes}\ncaptured: {video.captured_at}" if notes else f"captured: {video.captured_at}"
            if video.download_bytes is not None:
                mb = video.download_bytes / (1024 * 1024)
                notes = f"{notes}\nlocal video: {mb:.1f} MB" if notes else f"local video: {mb:.1f} MB"
            values = [
                str(video.rank),
                video.author_handle,
                video.video_path.name if video.video_path else video.video_url,
                notes,
            ]
            for col_idx, value in enumerate(values):
                item = self._readonly_item(value)
                item.setData(Qt.ItemDataRole.UserRole, video)
                if col_idx == 2 and video.screenshot_path is not None:
                    item.setIcon(QIcon(str(video.screenshot_path)))
                self.video_table.setItem(row_idx, col_idx, item)
        self.video_table.setSortingEnabled(True)
        has_videos = bool(record.associated_videos)
        if has_videos and not self.video_table.selectedItems():
            self.video_table.selectRow(0)
        self.open_video.setEnabled(has_videos)
        self.open_video_url.setEnabled(has_videos)

    def _selected_associated_video(self):
        selected = self.video_table.selectedItems()
        if not selected:
            return None
        row = selected[0].row()
        item = self.video_table.item(row, 0) or selected[0]
        return item.data(Qt.ItemDataRole.UserRole)

    def open_associated_video_from_item(self, item: QTableWidgetItem) -> None:
        self.video_table.selectRow(item.row())
        self.open_selected_associated_video()

    @staticmethod
    def _is_safe_web_url(url: str) -> bool:
        """Only http/https are safe to hand to the OS URL handler.

        Sound/video URLs ultimately come from third-party page scraping and
        redirect-following, so a malicious page could plant a non-web scheme
        (``file://``, a custom ``app:`` scheme, ``javascript:``) that
        QDesktopServices.openUrl would dispatch to the OS. Defense-in-depth.
        Local files are opened via QUrl.fromLocalFile and don't go through here.
        """
        from urllib.parse import urlparse

        try:
            return urlparse((url or "").strip()).scheme.lower() in ("http", "https")
        except (ValueError, TypeError):
            return False

    def _open_web_url(self, url: str, *, success_msg: str = "") -> bool:
        """Open an http/https URL externally; refuse anything else."""
        if not self._is_safe_web_url(url):
            self.statusBar().showMessage("Refused to open a non-web link (only http/https allowed)", 3000)
            return False
        QDesktopServices.openUrl(QUrl(url))
        if success_msg:
            self.statusBar().showMessage(success_msg, 2500)
        return True

    def open_selected_associated_video(self, *, prefer_url: bool = False) -> None:
        video = self._selected_associated_video()
        if video is None:
            return
        if not prefer_url and video.video_path is not None and video.video_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(video.video_path)))
            return
        if video.video_url:
            self._open_web_url(video.video_url)

    def open_selected_folder(self) -> None:
        if self.current_preview_record is None:
            return
        folder = self.current_preview_record.folder_path
        if folder is not None and folder.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def open_selected_tiktok_sound(self) -> None:
        if self.current_preview_record is None:
            self.statusBar().showMessage("No selected sound to open", 2500)
            return
        url = self._sound_url_for_record(self.current_preview_record)
        if url is None:
            self.statusBar().showMessage("No TikTok sound URL in metadata", 2500)
            return
        self._open_web_url(url, success_msg="Opened TikTok sound page")

    @staticmethod
    def _spotify_url_for_record(record) -> str | None:
        """The captured 'Add to Spotify' link for a record, if it's a real https
        spotify.com URL (the value is third-party-scraped, so validate the host)."""
        raw = getattr(record, "raw", None)
        url = str(raw.get("spotify_url") or "").strip() if isinstance(raw, dict) else ""
        if not url:
            return None
        from urllib.parse import urlparse

        try:
            parsed = urlparse(url)
        except (ValueError, TypeError):
            return None
        host = (parsed.hostname or "").lower()
        if parsed.scheme.lower() == "https" and (host == "spotify.com" or host.endswith(".spotify.com")):
            return url
        return None

    def open_selected_spotify(self) -> None:
        if self.current_preview_record is None:
            self.statusBar().showMessage("No selected sound to open", 2500)
            return
        url = self._spotify_url_for_record(self.current_preview_record)
        if url is None:
            self.statusBar().showMessage("This sound has no Spotify link", 2500)
            return
        self._open_web_url(url, success_msg="Opened in Spotify")

    def copy_selected_metadata(self) -> None:
        if self.current_preview_record is None:
            self.statusBar().showMessage("No selected sound to copy", 2500)
            return
        QApplication.clipboard().setText(self.vm.copyable_metadata(self.current_preview_record))
        self.statusBar().showMessage(f"Copied metadata for {self.current_preview_record.music_id}", 3000)

    def copy_selected_audio_path(self) -> None:
        if self.current_preview_record is None or self.current_preview_record.local_audio_path is None:
            self.statusBar().showMessage("No local audio path to copy", 2500)
            return
        QApplication.clipboard().setText(str(self.current_preview_record.local_audio_path))
        self.statusBar().showMessage("Copied local audio path", 2500)

    def copy_selected_canonical_url(self) -> None:
        if self.current_preview_record is None:
            self.statusBar().showMessage("No canonical URL to copy", 2500)
            return
        url = self._sound_url_for_record(self.current_preview_record)
        if url is None:
            self.statusBar().showMessage("No canonical URL to copy", 2500)
            return
        QApplication.clipboard().setText(url)
        self.statusBar().showMessage("Copied canonical URL", 2500)

    def toggle_favorite_by_id(self, music_id: str) -> None:
        is_favorite = self.vm.toggle_favorite(music_id)
        self.statusBar().showMessage(
            f"{'Added to' if is_favorite else 'Removed from'} Favorites: {music_id}",
            2200,
        )
        self.refresh_library_sidebar()
        # Update just the toggled row's star in place — a full refresh_table()
        # re-queries + re-sorts + rebuilds 2000+ QTableWidgetItems and beach-balls.
        # Only the Favorites view needs a real refresh (membership changed).
        if self.active_library_filter == "favorites":
            self.schedule_refresh_table()
            return
        # Repaint just the toggled row's star (model emits dataChanged for it).
        self.table.library_model().set_favorite(str(music_id), is_favorite)

    def open_library_context_menu(self, point) -> None:
        index = self.table.indexAt(point)
        if index.isValid():
            selected_rows = {i.row() for i in self.table.selectionModel().selectedRows()}
            if index.row() not in selected_rows:
                self.table.clearSelection()
                self.table.selectRow(index.row())
            self.table.setCurrentIndex(index)
            self.update_preview_from_selection()
        music_ids = self._selected_library_music_ids()
        music_id = music_ids[0] if music_ids else None
        count_label = f" ({len(music_ids)} selected)" if len(music_ids) > 1 else ""
        menu = QMenu(self)
        play_action = QAction("Play / pause", self)
        play_action.triggered.connect(self.play_selected_sound)
        favorite_action = QAction("★ Toggle favorite", self)
        favorite_action.triggered.connect(lambda: self.toggle_favorite_by_id(music_id or "") if music_id else None)
        mark_duplicate_action = QAction(f"Mark as Duplicate{count_label}", self)
        mark_duplicate_action.setEnabled(len(music_ids) >= 2)
        mark_duplicate_action.triggered.connect(lambda: self.mark_selected_library_as_duplicate(music_ids))
        copy_menu = QMenu("Copy", self)
        metadata_action = QAction("Metadata", self)
        metadata_action.triggered.connect(self.copy_selected_metadata)
        audio_path_action = QAction("Local audio path", self)
        audio_path_action.triggered.connect(self.copy_selected_audio_path)
        canonical_action = QAction("Canonical URL", self)
        canonical_action.triggered.connect(self.copy_selected_canonical_url)
        copy_menu.addAction(metadata_action)
        copy_menu.addAction(audio_path_action)
        copy_menu.addAction(canonical_action)
        open_sound_action = QAction("Open TikTok sound page", self)
        open_sound_action.triggered.connect(self.open_selected_tiktok_sound)
        folder_action = QAction("Open sound folder", self)
        folder_action.triggered.connect(self.open_selected_folder)
        add_to_menu = QMenu("Add to…", self)
        add_favorite_action = QAction("Favorites", self)
        add_favorite_action.triggered.connect(lambda: self.add_music_ids_to_library_target("favorites", music_ids))
        add_to_menu.addAction(add_favorite_action)
        for bin_row in self.vm.library_bins():
            action = QAction(bin_row.name, self)
            action.triggered.connect(
                lambda _checked=False, bin_id=bin_row.id: self.add_music_ids_to_library_target(f"bin:{bin_id}", music_ids)
            )
            add_to_menu.addAction(action)
        add_to_menu.addSeparator()
        new_bin_action = QAction("New sorting bin…", self)
        new_bin_action.triggered.connect(lambda: self.create_sorting_bin(seed_music_ids=music_ids))
        add_to_menu.addAction(new_bin_action)
        for action in (play_action, favorite_action):
            menu.addAction(action)
        menu.addMenu(add_to_menu)
        menu.addAction(mark_duplicate_action)
        menu.addSeparator()
        menu.addMenu(copy_menu)
        menu.addAction(open_sound_action)
        menu.addAction(folder_action)
        menu.exec(self.table.viewport().mapToGlobal(point))

    def mark_selected_library_as_duplicate(self, music_ids: tuple[str, ...] | list[str] | None = None) -> None:
        selected_ids = self._dedupe_music_ids(list(music_ids or self._selected_library_music_ids()))
        if len(selected_ids) < 2:
            self.statusBar().showMessage("Select at least two Library sounds to mark as a duplicate group", 3000)
            return
        try:
            group = self.vm.create_manual_duplicate_group(selected_ids)
        except ValueError as exc:
            QMessageBox.warning(self, "Could not create duplicate group", str(exc))
            return
        self.statusBar().showMessage(
            f"Added {len(group.candidates)} selected sound(s) to Duplicate Review: {group.group_id}",
            5000,
        )
        self.show_view("dedupe")
        self._select_duplicate_group(group.group_id)

    @staticmethod
    def _sound_url_for_record(record) -> str | None:
        raw = record.raw if isinstance(record.raw, dict) else {}
        for value in (
            record.canonical_url,
            record.source_music_url,
            raw.get("canonical_url"),
            raw.get("mobile_music_url"),
            raw.get("source_music_url"),
            raw.get("music_url"),
            raw.get("tiktok_music_url"),
            raw.get("share_url"),
            raw.get("url"),
        ):
            if isinstance(value, str) and value.startswith(("https://", "http://")) and "tiktok.com" in value:
                return value
        return None

    def play_record_by_id(self, music_id: str) -> None:
        record = self.records_by_id.get(str(music_id))
        if record is None:
            return
        for row_idx in range(self.table.rowCount()):
            if self.table.music_id_at(row_idx) == music_id:
                self.table.selectRow(row_idx)
                break
        self.current_preview_record = self.vm.preview_for(record.music_id)
        self.play_selected_sound()

    def play_selected_sound(self) -> None:
        if self.current_preview_record is None:
            return
        target = self.vm.play_target_for(self.current_preview_record)
        if target is None:
            self.playback_status.setText("No playable audio source")
            QMessageBox.information(self, "No playable audio", "This record has no local audio or preview URL yet.")
            return
        if not self._ensure_audio_player():
            QMessageBox.warning(self, "Playback unavailable", self.playback_status.text())
            return
        from PySide6.QtMultimedia import QMediaPlayer

        source = self._playback_source_for(target)
        if self._should_use_external_audio(target):
            if self._is_external_audio_playing() and self.external_audio_target == target:
                self.pause_playback()
                return
            if self.audio_player is not None:
                self.audio_player.stop()
            if self._play_external_audio(target):
                self._set_playing(self.current_preview_record.music_id)
                if hasattr(self, "transport_play_button"):
                    self.transport_play_button.setText("▮▮")
                    self.transport_play_button.setToolTip("Stop playback")
                return
        if self.audio_player.source() == source and self.audio_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.audio_player.pause()
            self.playback_status.setText("Playback paused")
            self._set_playing(None)
            return
        if self.audio_player.source() != source:
            self.audio_player.setSource(source)
        self.audio_player.play()
        self._set_playing(self.current_preview_record.music_id)
        self.playback_status.setText(f"Playing {self._playback_label_for(target)}")

    @staticmethod
    def _playback_source_for(target: Path | str) -> QUrl:
        return QUrl.fromLocalFile(str(target)) if isinstance(target, Path) else QUrl(target)

    @staticmethod
    def _playback_label_for(target: Path | str) -> str:
        return target.name if isinstance(target, Path) else "remote preview"

    @staticmethod
    def _duration_ms_for_record(record) -> int:
        if record.duration_seconds is None:
            return 0
        return max(0, int(record.duration_seconds * 1000))

    def _probe_audio_duration_ms(self, target: Path) -> int:
        """Read the true duration of an audio file via ffprobe (bundled with the same
        ffmpeg that provides ffplay), cached per path. Lets the scrubber work even when
        the sound's metadata never recorded a duration. Returns 0 if unavailable."""
        key = str(target)
        cached = self._duration_probe_cache.get(key)
        if cached is not None:
            return cached
        probed = 0
        ensure_media_tools_on_path()  # a Finder/launchd launch gets a stripped PATH
        ffprobe = shutil.which("ffprobe")
        if ffprobe is not None:
            try:
                out = subprocess.run(
                    [ffprobe, "-v", "quiet", "-of", "csv=p=0", "-show_entries", "format=duration", key],
                    capture_output=True, text=True, timeout=5, check=False,
                ).stdout.strip()
                probed = max(0, int(float(out) * 1000)) if out else 0
            except (OSError, ValueError, subprocess.SubprocessError):
                probed = 0
        self._duration_probe_cache[key] = probed
        return probed

    @staticmethod
    def _format_seconds(value: float) -> str:
        seconds = max(0, int(round(value)))
        minutes, seconds = divmod(seconds, 60)
        return f"{minutes}:{seconds:02d}"

    def _player_position_changed(self, position: int) -> None:
        if not self.progress_slider.isSliderDown():
            self.progress_slider.setValue(position)
        duration = self.audio_player.duration() if self.audio_player is not None else 0
        self.time_label.setText(f"{self._format_ms(position)} / {self._format_ms(duration)}")

    def _player_duration_changed(self, duration: int) -> None:
        self.progress_slider.setRange(0, max(0, duration))
        position = self.audio_player.position() if self.audio_player is not None else 0
        self.time_label.setText(f"{self._format_ms(position)} / {self._format_ms(duration)}")

    def _player_state_changed(self, state) -> None:
        from PySide6.QtMultimedia import QMediaPlayer

        playing = state == QMediaPlayer.PlaybackState.PlayingState
        if hasattr(self, "transport_play_button"):
            self.transport_play_button.setText("▮▮" if playing else "▶")
            self.transport_play_button.setToolTip("Pause playback" if playing else "Play selected sound")
        if playing and self.current_preview_record is not None:
            self._set_playing(self.current_preview_record.music_id)
        elif not playing:
            self._set_playing(None)

    def _player_media_status_changed(self, status) -> None:
        from PySide6.QtMultimedia import QMediaPlayer

        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.continuous_play_enabled:
            QTimer.singleShot(0, self._play_next_continuous_sound)

    def _player_error_occurred(self, _error, error_string: str = "") -> None:
        player_error = self.audio_player.errorString() if self.audio_player is not None else ""
        detail = error_string or player_error or "unknown media error"
        self.playback_status.setText(f"Playback error: {detail}")
        if hasattr(self, "transport_play_button"):
            self.transport_play_button.setText("▶")

    @staticmethod
    def _format_usage_count(value: int | None) -> str:
        if value is None:
            return "unknown"
        if value >= 1_000_000:
            return f"{value / 1_000_000:.1f}M videos"
        if value >= 1_000:
            return f"{value / 1_000:.1f}K videos"
        return f"{value:,} videos"

    @staticmethod
    def _format_ms(value: int) -> str:
        seconds = max(0, int(value // 1000))
        minutes, seconds = divmod(seconds, 60)
        return f"{minutes}:{seconds:02d}"

    def update_pairing_status(self) -> None:
        paired = bool(self.settings.relay_pair_code().strip())
        self.pairing_badge.set_state(paired=paired)
        self._refresh_pairing_card_visibility()

    def _refresh_pairing_card_visibility(self) -> None:
        # Always show the badge: a gradient checkmark when paired, a gentle
        # "open Settings to pair" nudge otherwise.
        self.pairing_badge.setVisible(True)

    def update_tiktok_status(self) -> None:
        if hasattr(self, "tiktok_badge"):
            self.tiktok_badge.set_status(tiktok_auth.connection_status())

    def open_tiktok_connect(self) -> None:
        TikTokConnectDialog(parent=self).exec()
        self.update_tiktok_status()

    def open_settings_dialog(self) -> None:
        dialog = SettingsDialog(settings=self.settings, parent=self)
        dialog.exec()
        self.update_pairing_status()
        self.update_tiktok_status()
        self._start_relay_auto_poll()  # begin polling if a pairing was just configured

    def refresh_inbox(self) -> None:
        self.current_inbox_rows = self.vm.pending_inbox()
        self.inbox_rows_by_id = {item.id: item for item in self.current_inbox_rows}
        self.inbox_label.setText(self.vm.inbox_text())
        self.inbox_table.setSortingEnabled(False)
        self.inbox_table.setRowCount(len(self.current_inbox_rows))
        for row_idx, item in enumerate(self.current_inbox_rows):
            received = getattr(item, "created_at", "") or getattr(item, "received_at", "") or ""
            err = getattr(item, "error", "") or ""
            for col_idx, value in enumerate([received, item.source, item.url, item.status, err]):
                table_item = QTableWidgetItem(str(value))
                table_item.setData(Qt.ItemDataRole.UserRole, item.id)
                if err:
                    table_item.setToolTip(err)
                self.inbox_table.setItem(row_idx, col_idx, table_item)
        self.inbox_table.setSortingEnabled(True)
        self.inbox_table.sortItems(0, Qt.SortOrder.DescendingOrder)

    def poll_relay_inbox(self) -> None:
        base_url = self.settings.relay_base_url()
        pair_code = self.settings.relay_pair_code()
        device_id = self.settings.relay_device_id()
        device_secret = self.settings.relay_device_secret()
        if not all((base_url, pair_code, device_id, device_secret)):
            QMessageBox.information(self, "Relay not configured", "Open Settings and create a relay pairing first.")
            return
        try:
            items = self.vm.poll_relay_inbox(
                base_url=base_url,
                pair_code=pair_code,
                device_id=device_id,
                device_secret=device_secret,
            )
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "Relay poll failed", str(exc))
            return
        self.refresh_inbox()
        QMessageBox.information(self, "Relay poll complete", f"Imported {len(items)} relay link(s).")

    def download_and_import(self) -> None:
        """Poll the relay, then download + ingest every pending link off the UI thread."""
        if getattr(self, "_import_worker", None) is not None and self._import_worker.isRunning():
            return
        reporter = None
        if self.settings.telemetry_enabled() and self.settings.relay_base_url():
            reporter = SaveEventReporter(
                base_url=self.settings.relay_base_url(),
                enabled=self.settings.telemetry_enabled(),
            )
        self._import_worker = _ImportWorker(
            self.vm,
            base_url=self.settings.relay_base_url(),
            pair_code=self.settings.relay_pair_code(),
            device_id=self.settings.relay_device_id(),
            device_secret=self.settings.relay_device_secret(),
            reporter=reporter,
            parent=self,
        )
        self._import_worker.importFinished.connect(self._on_import_finished)
        # Pipeline: queue each downloaded sound for transcription the moment it lands,
        # so ASR runs concurrently with the (throttled) download loop.
        self._import_worker.itemIngested.connect(self._on_item_ingested)
        self._ensure_transcribe_queue_worker()
        self._import_worker.start()
        # The bottom-left progress widget shows live status now; a persistent
        # showMessage here would obscure it (status-bar messages hide left widgets).
        self.import_progress_label.setText("Starting import…")
        self._import_progress_container.setVisible(True)
        self._import_progress_timer.start()
        self._tick_import_progress()

    def _on_import_finished(self, outcomes, error) -> None:
        self._import_worker = None
        if error:
            QMessageBox.warning(self, "Import failed", str(error))
            return
        self.refresh_inbox()
        # The ingest factory already upserted each new record to the index and
        # import_pending refreshed _records_by_id, so the new sounds are queryable
        # without a full vault rebuild — just refresh the table (the post-transcription
        # rebuild later surfaces transcripts). Avoids two full rebuilds per import.
        self.refresh_table()
        imported = sum(1 for o in outcomes if getattr(o, "status", "") == "ingested")
        duplicates = sum(1 for o in outcomes if getattr(o, "status", "") == "duplicate")
        failures = [o for o in outcomes if getattr(o, "status", "") == "failed"]
        # Transcription is no longer a post-import batch: each sound was queued for
        # ASR (via itemIngested) the moment it downloaded, so it runs concurrently and
        # the queue worker is likely still draining its backlog now. Nothing to start.
        parts = [f"Imported {imported} sound(s)"]
        if duplicates:
            parts.append(f"{duplicates} duplicate(s)")
        if failures:
            parts.append(f"{len(failures)} failed")
        self.statusBar().showMessage(", ".join(parts) + ".", 8000)
        # Per-item failures are returned in `outcomes` (the worker only raises on a
        # whole-batch crash), so surface them explicitly — otherwise a run where
        # every link failed reads as "Imported 0 sound(s)." and looks like success.
        if failures:
            sample = failures[0]
            reason = getattr(sample, "reason", "") or "unknown error"
            more = f"\n\n(+{len(failures) - 1} more)" if len(failures) > 1 else ""
            # If the failures look like a missing/expired TikTok session, route the
            # user straight to the fix instead of showing a dead-end error.
            tiktok_failures = [o for o in failures if self._looks_like_tiktok_auth_failure(o)]
            if tiktok_failures and not tiktok_auth.connection_status().connected:
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Icon.Warning)
                box.setWindowTitle("Connect TikTok to grab these sounds")
                box.setText(
                    f"{len(tiktok_failures)} TikTok sound(s) couldn't be saved because Sound "
                    "Cache isn't connected to a TikTok account.\n\n"
                    "TikTok only serves a sound's audio to a logged-in session — connect once "
                    "and these will import."
                )
                connect = box.addButton("Connect TikTok…", QMessageBox.ButtonRole.AcceptRole)
                box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
                box.exec()
                if box.clickedButton() is connect:
                    self.open_tiktok_connect()
                return
            QMessageBox.warning(
                self,
                "Some imports failed",
                f"{len(failures)} of {len(outcomes)} link(s) failed.\n\n"
                f"Example: {getattr(sample, 'url', '')}\n{reason}{more}",
            )

    @staticmethod
    def _looks_like_tiktok_auth_failure(outcome) -> bool:
        url = (getattr(outcome, "url", "") or "").lower()
        reason = (getattr(outcome, "reason", "") or "").lower()
        if "tiktok" not in url:
            return False
        return any(
            marker in reason
            for marker in (
                "no working app info",       # yt-dlp's broken sound extractor
                "playwright capture unavailable",  # no script/state wired
                "no audio captured",
                "no media captured",
                "fresh auth",
            )
        )

    def _start_transcription(self, targets, *, reason: str) -> None:
        """Transcribe a bounded batch (the just-imported sounds) in the background."""
        if _env_flag("SOUND_VAULT_DISABLE_TRANSCRIBE"):
            return
        if not targets:
            return
        worker = getattr(self, "_transcribe_worker", None)
        if worker is not None and worker.isRunning():
            return
        write_event("gui.transcription_started", reason=reason, count=str(len(targets)))
        self.worker_label.setText(f"Worker\ntranscribing 0/{len(targets)}…")
        self._transcribe_worker = _TranscriptionWorker(self.vm, targets, parent=self)
        self._transcribe_worker.progress.connect(self._on_transcription_progress)
        self._transcribe_worker.finished_ok.connect(self._on_transcription_finished)
        self._transcribe_worker.start()

    def _on_transcription_progress(self, done: int, total: int) -> None:
        self.worker_label.setText(f"Worker\ntranscribing {done}/{total}…")
        self.statusBar().showMessage(f"Transcribing sounds… {done}/{total}", 4000)

    def _on_transcription_finished(self, count: int, error) -> None:
        self._transcribe_worker = None
        self.worker_label.setText("Worker\nidle")
        if error is not None:
            write_event("gui.transcription_failed", **exception_fields(error))
            self.statusBar().showMessage("Transcription failed — see logs.", 6000)
            return
        write_event("gui.transcription_done", count=str(count))
        if count:
            self.rebuild_index()  # surface the new transcripts in the library + inspector
            self.statusBar().showMessage(f"Transcribed {count} sound(s).", 6000)

    # ---- concurrent transcription pipeline ----

    def _ensure_transcribe_queue_worker(self):
        """Lazily start the persistent transcription consumer (reused across imports).
        Returns None when ASR is disabled — the pipeline then quietly skips transcription."""
        if _env_flag("SOUND_VAULT_DISABLE_TRANSCRIBE"):
            return None
        worker = getattr(self, "_transcribe_queue_worker", None)
        if worker is not None and worker.isRunning():
            return worker
        if worker is not None:
            # A previous worker exited (e.g. a fatal in run()); reap it before
            # replacing so its thread is fully torn down (no orphaned QThread).
            worker.wait(100)
        worker = _TranscriptionQueueWorker(self.vm, parent=self)
        worker.transcribed.connect(self._on_queue_transcribed)
        worker.progress.connect(self._on_queue_progress)
        worker.start()
        self._transcribe_queue_worker = worker
        return worker

    def _on_item_ingested(self, music_id: str, folder: str, audio: str) -> None:
        """A sound just finished downloading — queue it for transcription right away."""
        worker = getattr(self, "_transcribe_queue_worker", None)
        if worker is not None and worker.isRunning():
            worker.enqueue(music_id, folder, audio)

    def _start_backlog_transcription(self, *, force: bool = False) -> None:
        """Feed every already-downloaded sound that still lacks a transcript into the
        queue worker, so the backlog drains in the background (concurrently with any
        import). Auto-runs once per session after the index is ready; the Vault menu
        action re-runs it on demand. Idempotent — transcribe_one skips sounds that
        already have a transcript."""
        if not force and getattr(self, "_backlog_transcription_started", False):
            return
        if _env_flag("SOUND_VAULT_DISABLE_TRANSCRIBE"):
            return
        worker = self._ensure_transcribe_queue_worker()
        if worker is None:
            if force:
                self.statusBar().showMessage("Transcription engine unavailable (faster-whisper not installed).", 6000)
            return
        targets = self.vm.transcription_targets()  # all pending across the vault
        self._backlog_transcription_started = True
        if not targets:
            if force:
                self.statusBar().showMessage("All sounds already transcribed. ✅", 5000)
            return
        for music_id, folder, audio in targets:
            worker.enqueue(str(music_id), str(folder), str(audio))
        write_event("gui.backlog_transcription_started", count=str(len(targets)))
        self.statusBar().showMessage(
            f"Transcribing {len(targets):,} sound(s) in the background — this can take a while.", 6000
        )

    def _on_queue_progress(self, done: int, enqueued: int) -> None:
        if done >= enqueued:
            self.worker_label.setText("Worker\nidle")
        else:
            self.worker_label.setText(f"Worker\ntranscribing {done}/{enqueued}…")

    def _on_queue_transcribed(self, music_id: str) -> None:
        # Coalesce a burst of new transcripts into one library refresh (~2.5s window)
        # so a long background drain doesn't churn the table on every item.
        self._library_needs_transcript_refresh = True
        if not self._transcript_refresh_timer.isActive():
            self._transcript_refresh_timer.start()

    def _flush_transcript_refresh(self) -> None:
        if not getattr(self, "_library_needs_transcript_refresh", False):
            return
        # transcribe_one already upserted each row to the index; only repaint when the
        # library is the visible view (avoids needless work mid-drain on other tabs).
        # Keep the flag SET while hidden so switching back to the library still flushes
        # the transcripts that landed in the background (see show_view).
        if self.stack.currentWidget() is self.library_view:
            self._library_needs_transcript_refresh = False
            self.refresh_table()

    # ---- global import progress bar + ETA ----

    def _setup_import_progress(self) -> None:
        """The global import progress: 'Importing X / Y · ~ETA' + a bar, anchored at the
        bottom-LEFT alongside the other status-bar output (addWidget = left side, not the
        far-right permanent slot). Driven off the file-backed inbox counts so it reflects
        true remaining work and survives restarts. Hidden until an import is active."""
        container = QWidget()
        container.setObjectName("importProgress")
        row = QHBoxLayout(container)
        row.setContentsMargins(2, 0, 8, 0)
        row.setSpacing(10)
        self.import_progress_label = QLabel("")
        self.import_progress_label.setObjectName("importProgressLabel")
        self.import_progress_bar = QProgressBar()
        self.import_progress_bar.setObjectName("importProgressBar")
        self.import_progress_bar.setTextVisible(False)
        self.import_progress_bar.setFixedWidth(220)
        self.import_progress_bar.setFixedHeight(12)
        row.addWidget(self.import_progress_label)
        row.addWidget(self.import_progress_bar)
        row.addStretch(1)
        self._import_progress_container = container
        container.setVisible(False)
        # Left side (next to where showMessage() statuses appear), not the far-right
        # permanent slot, so the import status sits with the other bottom-left output.
        self.statusBar().addWidget(container, 1)

    def _tick_import_progress(self) -> None:
        try:
            counts = self.vm.inbox_progress()
        except OSError:
            return  # transient FS/mount hiccup — try again next tick
        total = counts.get("total", 0)
        # "other" (any non-standard status) counts as processed so the bar always
        # reaches 100% — imported + failed + other + pending == total.
        processed = counts.get("imported", 0) + counts.get("failed", 0) + counts.get("other", 0)
        pending = counts.get("pending", 0)
        importing = getattr(self, "_import_worker", None) is not None and self._import_worker.isRunning()
        if total <= 0 or (not importing and pending <= 0):
            # Nothing in flight — stand down.
            self._import_progress_container.setVisible(False)
            self._import_progress_timer.stop()
            self._import_rate_samples.clear()
            return
        self._import_progress_container.setVisible(True)
        self.import_progress_bar.setMaximum(max(total, 1))
        self.import_progress_bar.setValue(min(processed, total))
        self._import_rate_samples.append((time.monotonic(), processed))
        label = f"Importing {processed:,} / {total:,}"
        eta = self._estimate_eta(pending)
        if eta:
            label += f"  ·  ~{eta} left"
        self.import_progress_label.setText(label)

    def _estimate_eta(self, remaining: int) -> str:
        """Rolling-window ETA: rate over the recent samples (adapts as throughput
        changes), not a naive whole-run average. Empty string until it's meaningful."""
        samples = self._import_rate_samples
        if remaining <= 0 or len(samples) < 2:
            return ""
        t0, p0 = samples[0]
        t1, p1 = samples[-1]
        dt = t1 - t0
        dp = p1 - p0
        if dt <= 0 or dp <= 0:
            return ""
        return self._format_duration(remaining / (dp / dt))

    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds = int(max(0, seconds))
        if seconds < 60:
            return f"{seconds}s"
        minutes, sec = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}m" if sec < 10 else f"{minutes}m {sec}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes}m"

    def refresh_review_queues(self) -> None:
        rows = self.vm.review_queue_rows()
        self.review_table.setSortingEnabled(False)
        self.review_table.setRowCount(len(rows))
        for row_idx, (queue, count, action, status_filter, media_filter) in enumerate(rows):
            for col_idx, value in enumerate([queue, f"{count:,}", action]):
                table_item = QTableWidgetItem(str(value))
                table_item.setData(Qt.ItemDataRole.UserRole, {"status_filter": status_filter, "media_filter": media_filter})
                if col_idx == 1:
                    table_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.review_table.setItem(row_idx, col_idx, table_item)
        self.review_table.setSortingEnabled(True)

    def apply_review_queue_filter(self, item: QTableWidgetItem) -> None:
        payload = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(payload, dict):
            return
        status_index = self.status_filter.findData(str(payload.get("status_filter") or "all"))
        if status_index >= 0:
            self.status_filter.setCurrentIndex(status_index)
        media_index = self.media_filter.findData(str(payload.get("media_filter") or "all"))
        if media_index >= 0:
            self.media_filter.setCurrentIndex(media_index)
        self.show_view("library")
        self.refresh_table()
        self.statusBar().showMessage("Applied review queue filter", 3000)

    def refresh_dedupe_review(self) -> None:
        previous_group_id = self._selected_duplicate_group_id()
        self.current_dedupe_groups = self.vm.duplicate_review_groups()
        self.dedupe_groups_by_id = {group.group_id: group for group in self.current_dedupe_groups}
        self.dedupe_groups_table.setSortingEnabled(False)
        self.dedupe_groups_table.setRowCount(len(self.current_dedupe_groups))
        for row_idx, group in enumerate(self.current_dedupe_groups):
            values = [group.group_id, f"{group.score:.2f}" if group.score else "—", str(len(group.candidates)), group.reason]
            for col_idx, value in enumerate(values):
                table_item = QTableWidgetItem(str(value))
                table_item.setData(Qt.ItemDataRole.UserRole, group.group_id)
                if col_idx in (1, 2):
                    table_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.dedupe_groups_table.setItem(row_idx, col_idx, table_item)
        self.dedupe_groups_table.setSortingEnabled(True)
        selected_row = -1
        if previous_group_id:
            for row_idx in range(self.dedupe_groups_table.rowCount()):
                item = self.dedupe_groups_table.item(row_idx, 0)
                if item and item.data(Qt.ItemDataRole.UserRole) == previous_group_id:
                    selected_row = row_idx
                    break
        if selected_row < 0 and self.current_dedupe_groups:
            selected_row = 0
        if selected_row >= 0:
            self.dedupe_groups_table.selectRow(selected_row)
        else:
            self.dedupe_candidates_table.setRowCount(0)
            self.clear_preview("Duplicate review complete")
        self.refresh_dedupe_candidates()

    def _selected_duplicate_group_id(self) -> str | None:
        selected = self.dedupe_groups_table.selectedItems()
        if not selected:
            return None
        row = selected[0].row()
        group_item = self.dedupe_groups_table.item(row, 0) or selected[0]
        value = group_item.data(Qt.ItemDataRole.UserRole)
        return str(value) if value else None

    def _select_duplicate_group(self, group_id: str) -> None:
        for row_idx in range(self.dedupe_groups_table.rowCount()):
            item = self.dedupe_groups_table.item(row_idx, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == group_id:
                self.dedupe_groups_table.selectRow(row_idx)
                self.refresh_dedupe_candidates()
                return

    def refresh_dedupe_candidates(self) -> None:
        group_id = self._selected_duplicate_group_id()
        group = self.dedupe_groups_by_id.get(str(group_id)) if group_id else None
        candidates = group.candidates if group is not None else ()
        self.dedupe_candidates_table.blockSignals(True)
        self.dedupe_candidates_table.setSortingEnabled(False)
        self.dedupe_candidates_table.setRowCount(len(candidates))
        for row_idx, candidate in enumerate(candidates):
            music_id = str(candidate.get("music_id") or "")
            play_cell = QPushButton("▶")
            play_cell.setObjectName("rowPlayButton")
            play_cell.setEnabled(self.vm.duplicate_candidate_play_target(candidate) is not None)
            play_cell.clicked.connect(lambda _checked=False, row=row_idx: self.play_dedupe_candidate(row))
            self.dedupe_candidates_table.setCellWidget(row_idx, 0, play_cell)
            audio_or_folder = candidate.get("local_audio_path") or candidate.get("folder") or ""
            values = ["", music_id, candidate.get("title") or "", candidate.get("artist") or "", audio_or_folder]
            for col_idx, value in enumerate(values):
                table_item = QTableWidgetItem(str(value))
                table_item.setData(Qt.ItemDataRole.UserRole, {"group_id": group_id, "music_id": music_id, "candidate": candidate})
                self.dedupe_candidates_table.setItem(row_idx, col_idx, table_item)
        self.dedupe_candidates_table.setSortingEnabled(True)
        self.dedupe_candidates_table.blockSignals(False)
        if candidates:
            self.dedupe_candidates_table.selectRow(0)
            self.update_preview_from_dedupe_selection()
        else:
            self.clear_preview("Select a duplicate candidate")

    def _selected_duplicate_candidate_payload(self) -> dict | None:
        selected = self.dedupe_candidates_table.selectedItems()
        if not selected:
            return None
        row = selected[0].row()
        item = self.dedupe_candidates_table.item(row, 1) or selected[0]
        payload = item.data(Qt.ItemDataRole.UserRole)
        return payload if isinstance(payload, dict) else None

    def update_preview_from_dedupe_selection(self) -> None:
        payload = self._selected_duplicate_candidate_payload()
        if not payload:
            return
        candidate = payload.get("candidate") or {}
        record = self.vm.duplicate_candidate_preview(candidate)
        if record is None:
            self.clear_preview("Duplicate candidate unavailable")
            return
        self._show_preview_record(record)

    def play_dedupe_candidate(self, row: int | None = None) -> None:
        if row is None:
            selected = self.dedupe_candidates_table.selectedItems()
            if not selected:
                return
            row = selected[0].row()
        self.dedupe_candidates_table.selectRow(row)
        self.update_preview_from_dedupe_selection()
        item = self.dedupe_candidates_table.item(row, 1)
        payload = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(payload, dict):
            return
        candidate = payload.get("candidate") or {}
        target = self.vm.duplicate_candidate_play_target(candidate)
        if target is None:
            self.playback_status.setText("Duplicate candidate has no playable audio")
            return
        if self._should_use_external_audio(target):
            if self._is_external_audio_playing() and self.external_audio_target == target:
                self.pause_playback()
                return
            if self.audio_player is not None:
                self.audio_player.stop()
            if self._play_external_audio(target):
                if hasattr(self, "transport_play_button"):
                    self.transport_play_button.setText("▮▮")
                    self.transport_play_button.setToolTip("Stop playback")
                return
        if self._ensure_audio_player():
            self.audio_player.setSource(self._playback_source_for(target))
            self.audio_player.play()
            self.playback_status.setText(f"Playing duplicate candidate {candidate.get('music_id')}")

    def record_selected_duplicate_decision(self, decision: str) -> None:
        group_id = self._selected_duplicate_group_id()
        group = self.dedupe_groups_by_id.get(str(group_id)) if group_id else None
        if group is None:
            self.statusBar().showMessage("No duplicate group selected", 2500)
            return
        candidate_ids = [str(candidate.get("music_id") or "") for candidate in group.candidates if candidate.get("music_id")]
        selected = self.dedupe_candidates_table.selectedItems()
        keep_music_id = ""
        if selected:
            item = self.dedupe_candidates_table.item(selected[0].row(), 1) or selected[0]
            payload = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(payload, dict):
                keep_music_id = str(payload.get("music_id") or "")
        if decision == "duplicates" and not keep_music_id:
            self.statusBar().showMessage("Select the keeper row before marking duplicates", 3000)
            return
        duplicate_music_ids = [music_id for music_id in candidate_ids if music_id != keep_music_id] if decision == "duplicates" else []
        self.vm.record_duplicate_decision(
            group_id=group.group_id,
            decision=decision,
            keep_music_id=keep_music_id,
            duplicate_music_ids=duplicate_music_ids,
            notes="Reviewed in Duplicate Review page.",
        )
        self.statusBar().showMessage(f"Recorded duplicate review: {decision}; removed group from queue", 3000)
        self.refresh_dedupe_review()

    def quarantine_selected_duplicates(self) -> None:
        group_id = self._selected_duplicate_group_id()
        group = self.dedupe_groups_by_id.get(str(group_id)) if group_id else None
        if group is None:
            self.statusBar().showMessage("No duplicate group selected", 2500)
            return
        selected = self.dedupe_candidates_table.selectedItems()
        if not selected:
            self.statusBar().showMessage("Select the keeper row before quarantining duplicates", 3000)
            return
        item = self.dedupe_candidates_table.item(selected[0].row(), 1) or selected[0]
        payload = item.data(Qt.ItemDataRole.UserRole)
        keep_music_id = str(payload.get("music_id") or "") if isinstance(payload, dict) else ""
        duplicate_music_ids = [
            str(candidate.get("music_id") or "")
            for candidate in group.candidates
            if candidate.get("music_id") and str(candidate.get("music_id")) != keep_music_id
        ]
        if not keep_music_id or not duplicate_music_ids:
            self.statusBar().showMessage("Need one keeper and at least one duplicate candidate", 3000)
            return
        response = QMessageBox.question(
            self,
            "Quarantine duplicate folders?",
            "The selected row is the keeper and stays in the Sound Cache. "
            "Only the other candidate folders in this group are moved into reports/duplicate-quarantine, "
            "so the action is reversible. "
            f"Keep {keep_music_id} and quarantine {len(duplicate_music_ids)} duplicate folder(s)?",
        )
        if response != QMessageBox.StandardButton.Yes:
            return
        result = self.vm.quarantine_duplicate_candidates(
            group_id=group.group_id,
            keep_music_id=keep_music_id,
            duplicate_music_ids=duplicate_music_ids,
        )
        moved = len(result.get("moved") or [])
        skipped = len(result.get("skipped") or [])
        self.statusBar().showMessage(f"Quarantined {moved} duplicate folder(s); skipped {skipped}", 5000)
        self.refresh_dedupe_review()
        self.rebuild_index()

    def refresh_worker_status(self) -> None:
        rows = self.vm.archive_health_rows()
        self.worker_status_table.setSortingEnabled(False)
        self.worker_status_table.setRowCount(len(rows))
        for row_idx, (metric, value) in enumerate(rows):
            self.worker_status_table.setItem(row_idx, 0, QTableWidgetItem(metric))
            self.worker_status_table.setItem(row_idx, 1, QTableWidgetItem(value))
        self.worker_status_table.setSortingEnabled(True)

    def _start_worker_job(self, name: str, future) -> None:
        if self._worker_future is not None and not self._worker_future.done():
            self.statusBar().showMessage(f"Worker already running: {self._worker_job_name}", 3000)
            return
        self._worker_job_name = name
        self._worker_future = future
        self.worker_label.setText(f"Worker\n{name}…")
        self._set_index_status("WORKER RUNNING", state="running")
        self._worker_timer.start()
        self.show_view("worker")

    def _finish_async_worker_job(self) -> None:
        if self._worker_future is None or not self._worker_future.done():
            return
        self._worker_timer.stop()
        name = self._worker_job_name
        try:
            result = self._worker_future.result()
            if isinstance(result, dict) and "scanned" in result:
                # Re-enrich incomplete metadata summary.
                self._worker_future = None
                self.worker_label.setText(
                    f"Worker\n{name} done • {result['enriched']:,} enriched / {result['scanned']:,} scanned"
                )
                self._set_index_status("INDEX READY", state="ready")
                QMessageBox.information(
                    self,
                    "Re-enrich complete",
                    "\n".join([
                        f"Scanned: {result['scanned']:,}",
                        f"Enriched: {result['enriched']:,}",
                        f"Unchanged: {result['unchanged']:,}",
                        f"Failed: {result['failed']:,}",
                    ]),
                )
                self.refresh_worker_status()
                if result["enriched"]:
                    self.rebuild_index()
                return
            summary = result.summary
            if hasattr(summary, "ok_count"):
                self.worker_label.setText(
                    f"Worker\n{name} done • {summary.ok_count:,} ok / {summary.error_count:,} errors"
                )
                QMessageBox.information(
                    self,
                    "oEmbed enrichment complete",
                    "\n".join(
                        [
                            f"Rows: {summary.record_count:,}",
                            f"OK: {summary.ok_count:,}",
                            f"Errors: {summary.error_count:,}",
                            f"Resumed: {summary.resumed_count:,}",
                            f"JSON: {result.json_path}",
                            f"CSV: {result.csv_path}",
                        ]
                    ),
                )
            else:
                self.worker_label.setText(
                    f"Worker\n{name} done • {summary.created_count:,} created / {summary.updated_count:,} updated"
                )
                QMessageBox.information(
                    self,
                    "Metadata packaging complete",
                    "\n".join(
                        [
                            f"Created: {summary.created_count:,}",
                            f"Updated existing: {summary.updated_count:,}",
                            f"Metadata-only catalog rows: {summary.metadata_only_count:,}",
                            f"Failures: {summary.failed_count:,}",
                            f"Catalog JSONL: {result.catalog_jsonl}",
                            f"Catalog CSV: {result.catalog_csv}",
                        ]
                    ),
                )
                self.rebuild_index()
            self.refresh_worker_status()
            self._set_index_status("WORKER COMPLETE", state="online")
        except Exception as exc:
            self.worker_label.setText(f"Worker\n{name} error: {exc}")
            self._set_index_status("WORKER ERROR", state="error")
            write_event("gui.worker_job_exception", job=name, **exception_fields(exc))
            QMessageBox.warning(self, "Worker failed", f"{name} failed:\n{exc}")
        finally:
            self._worker_future = None
            self._worker_job_name = ""

    def reenrich_incomplete_metadata(self) -> None:
        if self._worker_future is not None and not self._worker_future.done():
            self.statusBar().showMessage(f"Worker already running: {self._worker_job_name}", 3000)
            return
        incomplete = self.vm.sounds_needing_enrichment(limit=500)
        count = len(incomplete)
        if count == 0:
            QMessageBox.information(
                self, "Nothing to re-enrich",
                "Every sound with a TikTok URL already has artist, artwork, and popularity. ✨",
            )
            return
        cap = min(count, 50)
        choice = QMessageBox.question(
            self,
            "Re-enrich incomplete sounds",
            f"{count:,} sound(s) are missing artist, artwork, or popularity.\n\n"
            f"Re-scrape metadata for the first {cap} now? Each opens an authenticated "
            f"browser briefly (no audio re-download). Existing audio is untouched.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if choice != QMessageBox.StandardButton.Ok:
            return
        future = self.vm.reenrich_incomplete_async(limit=cap)
        self._start_worker_job(f"re-enrich {cap}", future)

    def run_oembed_enrichment(self) -> None:
        if self._worker_future is not None and not self._worker_future.done():
            self.statusBar().showMessage(f"Worker already running: {self._worker_job_name}", 3000)
            return
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "Choose normalized favorite-sounds JSON",
            str(self.vault_root / "catalog" / "imports"),
            "JSON files (*.json);;All files (*)",
        )
        if not selected:
            return
        future = self.vm.enrich_favorite_sounds_oembed_async(Path(selected))
        self._start_worker_job("oEmbed enrichment", future)

    def package_imported_metadata(self) -> None:
        if self._worker_future is not None and not self._worker_future.done():
            self.statusBar().showMessage(f"Worker already running: {self._worker_job_name}", 3000)
            return
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "Choose normalized or oEmbed-enriched favorite-sounds JSON",
            str(self.vault_root / "catalog" / "imports"),
            "JSON files (*.json);;All files (*)",
        )
        if not selected:
            return
        future = self.vm.package_imported_sounds_async(Path(selected))
        self._start_worker_job("metadata packaging", future)

    def mark_selected_inbox_imported(self) -> None:
        selected = self.inbox_table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        id_item = self.inbox_table.item(row, 2) or selected[0]
        item_id = id_item.data(Qt.ItemDataRole.UserRole)
        inbox_item = self.inbox_rows_by_id.get(str(item_id))
        if inbox_item is None:
            return
        self.vm.mark_inbox_imported(inbox_item.id)
        self.refresh_inbox()

    def import_tiktok_favorite_sounds_export(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "Import TikTok favorite sounds export",
            self._vault_dialog_start_dir(),
            "JSON files (*.json);;All files (*)",
        )
        if not selected:
            return
        try:
            result = self.vm.import_favorite_sounds_export(Path(selected))
        except Exception as exc:  # noqa: BLE001 - surface to the user
            write_event("gui.favorite_sounds_import_exception", **exception_fields(exc))
            QMessageBox.warning(self, "Import failed", f"Could not import TikTok favorite sounds export:\n{exc}")
            return
        summary = result.summary
        # The step above only writes a metadata analysis to catalog/imports — it
        # fetches no audio. Also queue the favorites so the user can actually
        # Download & import them (this is what they almost always want).
        try:
            queued = int(self.vm.import_sound_pack(Path(selected)).get("queued", 0))
        except Exception as exc:  # noqa: BLE001 - queuing is best-effort
            write_event("gui.favorite_sounds_queue_exception", **exception_fields(exc))
            queued = 0
        self.refresh_inbox()
        self.show_view("inbox")
        self.statusBar().showMessage(
            f"Imported {summary.record_count:,} favorite sounds • queued {queued:,} for download", 6000
        )
        self.inbox_label.setText(
            "TikTok favorites\n"
            f"{summary.record_count:,} rows • {queued:,} queued\n"
            f"{summary.already_in_vault:,} already in vault"
        )
        if queued:
            resp = QMessageBox.question(
                self,
                "Favorites queued for download",
                f"Found {summary.unique_music_ids:,} favorite sounds and queued {queued:,} for download "
                f"({summary.already_in_vault:,} already in your vault).\n\n"
                "Download + import them now? (This fetches each with your TikTok session — "
                "a large library can take a while.)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if resp == QMessageBox.StandardButton.Yes:
                self.download_and_import()
        else:
            QMessageBox.information(
                self,
                "TikTok favorites imported",
                f"All {summary.record_count:,} favorites are already queued or in your vault.\n\n"
                f"Metadata analysis written to {result.summary_path}",
            )

    def handle_soundcache_url(self, url: str) -> None:
        """Handle a `soundcache://ingest?…` deep link from the website's
        'Get this sound' button: queue the sound + offer to fetch it now."""
        from sound_vault.ingest.deeplink import parse_soundcache_url

        link = parse_soundcache_url(url)
        write_event("gui.deeplink_received", ok=str(bool(link)))
        if link is None:
            return
        # Bring the app to the foreground (the click happened in the browser).
        self.show()
        self.raise_()
        self.activateWindow()
        self.vm.inbox.add_url(
            link.music_url, source="web:hits", relay_id=f"web:{link.sound_id}", note=link.title
        )
        self.show_view("inbox")
        self.refresh_inbox()
        label = link.title or link.music_url
        self.statusBar().showMessage(f"Queued “{label}” from the website.", 6000)
        resp = QMessageBox.question(
            self,
            "Sound queued",
            f"Queued “{label}” from soundcache.io.\n\nDownload + import it now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp == QMessageBox.StandardButton.Yes:
            self.download_and_import()

    def import_sound_pack(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "Import sound pack or TikTok data export",
            self._vault_dialog_start_dir(),
            "JSON files (*.json);;All files (*)",
        )
        if not selected:
            return
        try:
            summary = self.vm.import_sound_pack(Path(selected))
        except Exception as exc:  # noqa: BLE001 - surface to the user
            write_event("gui.pack_import_exception", **exception_fields(exc))
            QMessageBox.warning(self, "Import failed", f"Could not import sound pack:\n{exc}")
            return
        self.refresh_inbox()
        self.show_view("inbox")
        queued, skipped, rejected = summary["queued"], summary["skipped"], summary["rejected"]
        parts = [f"Queued {queued} sound(s)"]
        if skipped:
            parts.append(f"{skipped} already queued")
        if rejected:
            parts.append(f"{rejected} rejected (unsafe/unsupported URL)")
        self.statusBar().showMessage(", ".join(parts) + ".", 8000)
        if not queued:
            QMessageBox.information(
                self, "Nothing to queue", "No new sounds were added (already queued, or no valid links in the file)."
            )
            return
        resp = QMessageBox.question(
            self,
            "Sound pack queued",
            f"Queued {queued} sound(s) from: {', '.join(summary['packs'])}.\n\nDownload + import them now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp == QMessageBox.StandardButton.Yes:
            self.download_and_import()


STYLESHEET = """
/* ============================================================
   SOUND CACHE — LIQUID CHROME DESIGN SYSTEM v3
   Deep violet-night surfaces; holographic accents (cyan / lilac /
   pink / gold) on interactive + status; glossy highlights. Stays
   dense + legible — a vibrant brand wrapping a serious tool.
   Palette: night #0a0518/#150a33, ink #fbedff, muted #b3a3d6,
   holo cyan #66ecff lilac #b793ff pink #ff6ad5 gold #ffd86b.
   ============================================================ */

QMainWindow, QWidget {
    background: #0a0518;
    color: #d9cef0;
    font-family: "Quicksand", "Quicksand", "SF Pro Display", "Helvetica Neue", Arial, sans-serif;
    font-size: 12px;
}

/* Shell & main surfaces — violet night */
#appShell { background: #05030f; }
#mainDeck {
    background-color: #0a0518;
    background-image: url("__DECK_BG__");
    background-repeat: no-repeat;
    background-position: top center;
    border-left: 1px solid #2a1758;
    border-right: 1px solid #1a0c33;
}
#sidebar {
    background-color: #0a0518;
    background-image: url("__SIDEBAR_BG__");
    background-repeat: no-repeat;
    background-position: top left;
    border-right: 1px solid #2a1758;
}

/* Brand / section headers */
#brand {
    padding: 2px 4px 12px 4px;
    border-bottom: 1px solid #2a1758;
    background: transparent;
}
#brandText {
    font-family: "Unbounded", "Quicksand", sans-serif;
    font-size: 16px;
    font-weight: 700;
    color: #fbedff;
    letter-spacing: -0.01em;
    background: transparent;
}
#brandMark { background: transparent; }
#sourceGroup {
    color: #7d6fa9;
    background: transparent;
    border: none;
    padding: 10px 6px 4px 6px;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 2.4px;
    text-transform: uppercase;
}

/* Title typography */
#title { font-family: "Unbounded", "Quicksand", sans-serif; font-size: 17px; font-weight: 700; color: #fbedff; letter-spacing: -0.01em; background: transparent; }
#sectionTitle { font-family: "Quicksand", sans-serif; font-size: 14px; font-weight: 600; color: #fbedff; background: transparent; }
#previewTitle { font-family: "Quicksand", sans-serif; font-size: 16px; font-weight: 600; color: #fbedff; background: transparent; }
#muted { color: #8c7fb5; font-size: 11px; background: transparent; }
#onboardBody { color: #d9cef0; font-size: 12px; line-height: 150%; background: transparent; }

/* Chrome header strip — glossy holo sheen */
#chromeHeader {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #1b0f3d, stop:0.5 #2a1860, stop:1 #1b0f3d);
    border-top: 1px solid #5a3fa0;
    border-bottom: 1px solid #0a0518;
    border-left: 1px solid #2a1758;
    border-right: 1px solid #1a0c33;
    border-radius: 14px;
}
#transportDeck {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #20123f, stop:0.5 #170c33, stop:1 #100826);
    border-top: 1px solid #4a2f86;
    border-bottom: 1px solid #050310;
    border-left: 1px solid #1a0c33;
    border-right: 1px solid #1a0c33;
    border-radius: 12px;
}

/* Transport buttons — dark glass, holo on hover */
#transportButton {
    min-width: 34px;
    min-height: 30px;
    border-radius: 9px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #2a1858, stop:1 #170c33);
    color: #f0e6ff;
    border: 1px solid #1a0c33;
    border-top: 1px solid #4a2f86;
    padding: 4px;
    text-align: center;
    font-weight: 700;
}
#transportButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #66ecff, stop:1 #b793ff);
    color: #0a0518;
    border: 1px solid #66ecff;
}
#transportButton:pressed {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #ff6ad5, stop:1 #b793ff);
    color: #0a0518;
}
#transportButton:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #66ecff, stop:1 #ff6ad5);
    color: #0a0518;
    border: 1px solid #ffffff;
    font-weight: 700;
}

/* Now-Playing HUD capsule */
#capsuleDisplay {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #100826, stop:0.06 #150a33, stop:1 #1b0f3d);
    border: 1px solid #0a0518;
    border-top: 1px solid #5a3fa0;
    border-radius: 12px;
}
#displayEyebrow {
    color: #66ecff;
    font-family: "SF Mono", "Menlo", "Consolas", monospace;
    font-size: 9px;
    font-weight: 600;
    letter-spacing: 1.8px;
    text-transform: uppercase;
    background: transparent;
}
#displayTitle {
    color: #fbedff;
    font-family: "Quicksand", "Quicksand", sans-serif;
    font-size: 14px;
    font-weight: 600;
    background: transparent;
}
#displaySubtitle {
    color: #8c7fb5;
    font-family: "SF Mono", "Menlo", "Consolas", monospace;
    font-size: 10px;
    background: transparent;
}

/* Portal tabs (vestigial) */
#libraryTabs {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #1b0f3d, stop:1 #150a33);
    border: 1px solid #2a1758;
    border-radius: 8px;
}
#portalTab {
    color: #b3a3d6;
    background: transparent;
    border: 1px solid #2a1758;
    border-radius: 8px;
    padding: 5px 12px;
    font-family: "Quicksand", sans-serif;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.5px;
}
#portalTab:checked, #portalTab:hover {
    color: #0a0518;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #66ecff, stop:1 #b793ff);
    border: 1px solid #66ecff;
}

/* Status pill */
#limeStatusPanel {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #1b0f3d, stop:1 #150a33);
    border: 1px solid #2a1758;
    border-top: 1px solid #5a3fa0;
    border-radius: 14px;
    padding: 4px 12px;
}
#limeStatusDot { background: transparent; }
#statusReadout {
    color: #66ecff;
    font-family: "SF Mono", "Menlo", "Consolas", monospace;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.6px;
    text-transform: uppercase;
    background: transparent;
}

/* Global import progress (status-bar permanent widget) */
#importProgress { background: transparent; }
#importProgressLabel {
    color: #c5b3e6;
    font-family: "SF Mono", "Menlo", "Consolas", monospace;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.3px;
    background: transparent;
}
#importProgressBar {
    background: #150a33;
    border: 1px solid #2a1758;
    border-radius: 6px;
}
#importProgressBar::chunk {
    border-radius: 6px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #66ecff, stop:0.55 #b793ff, stop:1 #ff6ad5);
}

/* "Open in Spotify" — only shown for sounds with a captured Spotify link */
#spotifyButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1ed760, stop:1 #1db954);
    color: #06210f;
    font-weight: 700;
    border: 1px solid #18a449;
    border-top: 1px solid #4ff08a;
}
#spotifyButton:hover { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #28e06b, stop:1 #1ec45e); }

/* Right inspector */
#preview {
    background-color: #0a0518;
    background-image: url("__DECK_BG__");
    background-repeat: no-repeat;
    background-position: top right;
    border-left: 1px solid #2a1758;
}
#artwork {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #66ecff, stop:0.5 #b793ff, stop:1 #ff6ad5);
    color: #1a0526;
    border: 1px solid #0a0518;
    border-top: 1px solid #ffb3ec;
    border-radius: 16px;
}

/* Generic buttons — glassy violet */
QPushButton, QToolButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #2a1860, stop:0.5 #20123f, stop:1 #170c33);
    color: #f0e6ff;
    border: 1px solid #1a0c33;
    border-top: 1px solid #4a2f86;
    border-radius: 10px;
    padding: 6px 12px;
    text-align: left;
    font-weight: 600;
}
QPushButton:hover, QToolButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #3a2480, stop:0.5 #2a1860, stop:1 #20123f);
    border-top: 1px solid #66ecff;
}
QPushButton:pressed, QToolButton:pressed {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #170c33, stop:1 #2a1860);
    border-color: #66ecff;
}
QPushButton:disabled, QToolButton:disabled {
    background: #160c30;
    color: #5a4f7a;
    border-color: #1a0c33;
    border-top-color: #2a1758;
}
QToolButton::menu-indicator { image: none; width: 0; height: 0; }
QToolButton::menu-button { border: none; width: 0; }
#columnsButton { padding-right: 10px; }

#dangerButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #ff6ad5, stop:0.5 #d63a9e, stop:1 #8a1f63);
    color: #fff0fa;
    border: 1px solid #5a1240;
    border-top: 1px solid #ffb3ec;
    text-align: center;
    font-weight: 700;
}
#dangerButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #ff86df, stop:0.5 #e64aae, stop:1 #9a2f73);
}

#rowPlayButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #66ecff, stop:1 #b793ff);
    color: #0a0518;
    border: 1px solid #0a0518;
    border-radius: 9px;
    padding: 2px 8px;
    text-align: center;
    font-weight: 700;
}

/* Primary action — full holo */
#primaryButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #ff6ad5, stop:0.4 #b793ff, stop:0.75 #66ecff, stop:1 #ffd86b);
    color: #1a0526;
    border: 1px solid #1a0c33;
    border-top: 1px solid #fff4ff;
    text-align: center;
    font-family: "Quicksand", sans-serif;
    font-weight: 700;
}
#primaryButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #ff86df, stop:0.4 #c7a8ff, stop:0.75 #8af2ff, stop:1 #ffe389);
}
#primaryButton:pressed {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #ffd86b, stop:0.4 #66ecff, stop:1 #ff6ad5);
}

/* Sidebar nav */
#navButton {
    background: transparent;
    color: #b3a3d6;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 7px 11px;
    text-align: left;
    font-family: "Quicksand", sans-serif;
    font-weight: 500;
}
#navButton:hover {
    background: rgba(183, 147, 255, 0.10);
    color: #fbedff;
    border: 1px solid #2a1758;
}
#navButton[active="true"] {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(102,236,255,0.18), stop:1 rgba(255,106,213,0.12));
    color: #fbedff;
    border: 1px solid #5a3fa0;
    border-left: 2px solid #66ecff;
}

#smallAddButton {
    min-width: 28px;
    max-width: 32px;
    text-align: center;
    font-size: 15px;
    font-weight: 700;
    padding: 4px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #2a1860, stop:1 #170c33);
    color: #66ecff;
    border: 1px solid #1a0c33;
    border-top: 1px solid #4a2f86;
    border-radius: 10px;
}

#libraryBinButton {
    text-align: left;
    padding: 5px 9px;
    border-radius: 9px;
    color: #b3a3d6;
    background: transparent;
    border: 1px solid transparent;
    font-weight: 500;
}
#libraryBinButton:hover {
    background: rgba(183, 147, 255, 0.10);
    color: #fbedff;
    border: 1px solid #2a1758;
}
#libraryBinButton[active="true"],
#libraryBinButton[dropHover="true"] {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(102,236,255,0.18), stop:1 rgba(255,106,213,0.12));
    color: #fbedff;
    border: 1px solid #5a3fa0;
    border-left: 2px solid #ff6ad5;
}

/* Cards & group boxes */
#pairingCard, #statCard, QGroupBox {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #170c39, stop:0.04 #140a30, stop:1 #0e0724);
    border: 1px solid #2a1758;
    border-top: 1px solid #3a2470;
    border-radius: 12px;
    padding: 10px;
    color: #b3a3d6;
}
QGroupBox { margin-top: 10px; font-weight: 600; font-size: 12px; }
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: #66ecff;
    background: transparent;
    text-transform: uppercase;
    font-family: "Quicksand", sans-serif;
    font-size: 9px;
    letter-spacing: 1.6px;
    font-weight: 700;
}

/* Inputs */
QLineEdit, QComboBox {
    background: #0e0724;
    border: 1px solid #2a1758;
    border-top: 1px solid #1a0c33;
    border-radius: 10px;
    padding: 6px 10px;
    color: #f0e6ff;
    selection-background-color: #b793ff;
    selection-color: #0a0518;
}
QLineEdit:focus, QComboBox:focus {
    border: 1px solid #66ecff;
    border-top: 1px solid #66ecff;
}
QComboBox::drop-down { width: 18px; border: none; background: transparent; }
QComboBox::down-arrow {
    image: none;
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #b793ff;
    margin-right: 6px;
}
QComboBox QAbstractItemView {
    background: #150a33;
    color: #d9cef0;
    border: 1px solid #2a1758;
    outline: 0;
    padding: 4px;
    selection-background-color: #b793ff;
    selection-color: #0a0518;
}
QComboBox QAbstractItemView::item { min-height: 26px; padding: 6px 10px; }
QComboBox QAbstractItemView::item:selected,
QComboBox QAbstractItemView::item:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #66ecff, stop:1 #b793ff);
    color: #0a0518;
}

#searchBox {
    border-radius: 13px;
    padding: 4px 14px;
    background: #0e0724;
    border: 1px solid #2a1758;
    border-top: 1px solid #1a0c33;
    color: #f0e6ff;
}
#searchBox:focus { border: 1px solid #66ecff; border-top: 1px solid #66ecff; }

/* Table — data stays crisp + legible; transparent so the deck's sparkle sky
   shows through (translucent zebra keeps the rows readable). */
QTableView, QTableWidget {
    background: transparent;
    alternate-background-color: rgba(20, 11, 48, 0.42);
    border: 1px solid #2a1758;
    gridline-color: #221140;
    selection-background-color: #b793ff;
    selection-color: #ffffff;
    color: #d9cef0;
    font-family: "SF Pro Text", "Helvetica Neue", "Segoe UI", sans-serif;
}
QTableView::item, QTableWidget::item { padding: 4px 8px; border: none; background: transparent; }
QTableView::item:hover, QTableWidget::item:hover { background: rgba(183, 147, 255, 0.12); }
QTableView::item:selected, QTableWidget::item:selected {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(102,236,255,0.28), stop:1 rgba(255,106,213,0.20));
    color: #ffffff;
}
QHeaderView::section {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #20123f, stop:1 #150a33);
    color: #b3a3d6;
    padding: 6px 8px;
    border: none;
    border-right: 1px solid #0c0720;
    border-bottom: 1px solid #0a0518;
    border-top: 1px solid #3a2470;
    font-family: "Quicksand", sans-serif;
    font-weight: 600;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 1.2px;
}

/* Transcript / text editors */
QTextEdit {
    background: #0e0724;
    border: 1px solid #2a1758;
    border-top: 1px solid #3a2470;
    border-radius: 12px;
    padding: 10px;
    color: #e7d9ff;
    font-family: "SF Mono", "Menlo", "Consolas", monospace;
    font-size: 11px;
    selection-background-color: #b793ff;
    selection-color: #0a0518;
}

/* Menus */
QMenu {
    background: #150a33;
    color: #d9cef0;
    border: 1px solid #2a1758;
    border-top: 1px solid #3a2470;
    padding: 6px;
    font-size: 12px;
}
QMenu::item { min-height: 26px; padding: 6px 20px 6px 14px; border-radius: 8px; }
QMenu::item:selected {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #66ecff, stop:1 #b793ff);
    color: #0a0518;
}
QMenu::separator { height: 1px; background: #2a1758; margin: 6px 4px; }

/* Slider — holo groove fill via handle */
QSlider::groove:horizontal {
    height: 6px;
    background: #0e0724;
    border: 1px solid #1a0c33;
    border-radius: 3px;
}
QSlider::sub-page:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #66ecff, stop:1 #ff6ad5);
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #ffffff, stop:1 #b793ff);
    border: 1px solid #0a0518;
    width: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::handle:horizontal:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #ffffff, stop:1 #66ecff);
}

/* Scrollbars */
QScrollBar:vertical { background: #0a0518; width: 11px; border: none; }
QScrollBar::handle:vertical {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #3a2470, stop:1 #2a1758);
    border-radius: 5px;
    min-height: 24px;
    margin: 2px;
}
QScrollBar::handle:vertical:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #b793ff, stop:1 #66ecff);
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #0a0518; height: 11px; border: none; }
QScrollBar::handle:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #3a2470, stop:1 #2a1758);
    border-radius: 5px;
    min-width: 24px;
    margin: 2px;
}
QScrollBar::handle:horizontal:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #b793ff, stop:1 #66ecff);
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

/* Archive Health HUD */
#archiveHealthPanel {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #100826, stop:0.06 #150a33, stop:1 #1b0f3d);
    border: 1px solid #2a1758;
    border-top: 1px solid #3a2470;
    border-radius: 12px;
}
#archiveHealthTitle {
    color: #66ecff;
    font-family: "Quicksand", "SF Mono", monospace;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 1.8px;
    text-transform: uppercase;
    background: transparent;
}
#archiveHealthLabel {
    color: #b3a3d6;
    font-family: "SF Mono", "Menlo", "Consolas", monospace;
    font-size: 10px;
    background: transparent;
}
#archiveHealthValue {
    color: #fbedff;
    font-family: "SF Mono", "Menlo", "Consolas", monospace;
    font-size: 10px;
    font-weight: 600;
    background: transparent;
}
"""


class _DeepLinkBus(QObject):
    """Funnels `soundcache://` URLs (from macOS open-URL events or a forwarding
    second instance) to a single slot, buffering any that arrive before a
    listener is connected (cold-start scheme launches deliver very early)."""

    url = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._buffer: list[str] = []
        self._live = False

    def push(self, url: str) -> None:
        if not url:
            return
        if self._live:
            self.url.emit(url)
        else:
            self._buffer.append(url)

    def go_live(self) -> None:
        self._live = True
        for url in self._buffer:
            self.url.emit(url)
        self._buffer.clear()


class _FileOpenFilter(QObject):
    """macOS delivers custom-scheme opens as a QFileOpenEvent (Apple Event)."""

    def __init__(self, bus: _DeepLinkBus) -> None:
        super().__init__()
        self._bus = bus

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt signature)
        if event.type() == QEvent.Type.FileOpen:
            url = event.url().toString() if event.url().isValid() else event.file()
            if url:
                self._bus.push(url)
                return True
        return False


def _single_instance_name() -> str:
    try:
        return f"sound-cache-{getpass.getuser()}"
    except Exception:  # noqa: BLE001 - getuser can raise on odd environments
        return "sound-cache"


_UI_DIR = Path(__file__).resolve().parent


def _load_brand_fonts() -> None:
    """Register the bundled brand faces so the stylesheet's Unbounded (display) +
    Quicksand (body) actually render — neither is a system font."""
    from PySide6.QtGui import QFontDatabase

    for name in ("Unbounded.ttf", "Quicksand.ttf"):
        path = _UI_DIR / "fonts" / name
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))


def app_icon() -> QIcon:
    for name in ("app-icon.png", "app-icon-256.png"):
        path = _UI_DIR / "assets" / name
        if path.exists():
            return QIcon(str(path))
    return QIcon()


def run_desktop(vault_root: Path | None = None, pending_urls: list[str] | None = None) -> int:
    pending_urls = [u for u in (pending_urls or []) if u]
    app = QApplication.instance() or QApplication(sys.argv)
    _load_brand_fonts()
    app.setApplicationName("Sound Cache")
    app.setApplicationDisplayName("Sound Cache")
    app.setWindowIcon(app_icon())

    bus = _DeepLinkBus()
    open_filter = _FileOpenFilter(bus)
    app.installEventFilter(open_filter)

    server_name = _single_instance_name()
    probe = QLocalSocket()
    probe.connectToServer(server_name)
    if probe.waitForConnected(250):
        # A primary instance already owns the window — forward our URL(s) to it and exit
        # so the click lands in the running app instead of opening a second window.
        captured = list(pending_urls)
        bus.url.connect(captured.append)
        bus.go_live()
        if not captured:
            # Fresh process spawned by macOS for the scheme — its URL arrives via a
            # FileOpen event; spin briefly to catch it before forwarding.
            QTimer.singleShot(500, app.quit)
            app.exec()
        payload = "\n".join(u for u in captured if u)
        if payload:
            probe.write((payload + "\n").encode("utf-8"))
            probe.flush()
            probe.waitForBytesWritten(1000)
        probe.disconnectFromServer()
        return 0
    probe.abort()

    # We are the primary instance: own the single-instance server.
    QLocalServer.removeServer(server_name)
    server = QLocalServer()
    server.listen(server_name)

    window = SoundVaultWindow(vault_root=vault_root)

    def _on_forwarded() -> None:
        conn = server.nextPendingConnection()
        if conn is not None and conn.waitForReadyRead(1000):
            data = bytes(conn.readAll()).decode("utf-8", "ignore")
            for line in data.splitlines():
                if line.strip():
                    window.handle_soundcache_url(line.strip())

    server.newConnection.connect(_on_forwarded)
    bus.url.connect(window.handle_soundcache_url)
    # Keep refs alive for the process lifetime.
    window._deeplink_server = server  # type: ignore[attr-defined]
    window._deeplink_bus = bus  # type: ignore[attr-defined]
    window._deeplink_filter = open_filter  # type: ignore[attr-defined]

    window.show()
    for url in pending_urls:
        QTimer.singleShot(0, lambda u=url: window.handle_soundcache_url(u))
    bus.go_live()  # flush any FileOpen events buffered during cold start
    return app.exec()
