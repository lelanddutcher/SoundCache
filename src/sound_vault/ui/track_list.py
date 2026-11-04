"""The Library track list: one painted row per sound (design boards 1, 3 and 4).

Rows are painted by :class:`TrackRowDelegate` on top of the existing
``LibraryTableModel``; :class:`TrackListView` is a ``QListView`` that shares the
table's model *and* selection model, so every existing handler (preview pane,
context menu, drag-out, play, favorites) keeps working whichever view is showing.

Painting never touches the disk on the hot path: artwork thumbnails and waveform
peaks come from small LRU caches filled on first sight.

Layout is responsive. Columns drop out as the row narrows (tags first, then uses,
then the waveform) so nothing ever overlaps or clips mid-glyph; see
:func:`row_layout`, which tests assert against at several widths.
"""
from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable, Protocol

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QImageReader,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QListView,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolTip,
    QWidget,
)

from sound_vault.vault.analysis import load_peaks

# ---------------------------------------------------------------- tokens (board 2)
NIGHT = QColor("#0a0518")
NIGHT_2 = QColor("#150a33")
RAISED = QColor("#1d1042")
INK = QColor("#fbedff")
MUTED = QColor("#c5b3e6")
LABEL = QColor("#a993d6")
WAVE_OFF = QColor("#4a3d72")
LILAC = QColor("#b793ff")
PINK = QColor("#ff6ad5")
CYAN = QColor("#66ecff")
GOLD = QColor("#ffd86b")
HOVER = QColor(255, 255, 255, 14)
TILES = [QColor(c) for c in ("#3b1d6e", "#1d3b6e", "#6e1d4f", "#1d5a6e", "#5a3b1d", "#2d1d6e", "#6e3b1d", "#1d6e4a", "#4f1d6e", "#6e1d2d")]

# Same values as desktop.py's PLAYABLE_ROLE / FAVORITE_ROLE (the model serves both views).
PLAYABLE_ROLE = int(Qt.ItemDataRole.UserRole) + 1
FAVORITE_ROLE = int(Qt.ItemDataRole.UserRole) + 2
RECORD_ROLE = int(Qt.ItemDataRole.UserRole) + 40
ROW_HEIGHT = {"comfortable": 76, "compact": 56}
ART_SIZE = {"comfortable": 56, "compact": 40}
NEW_WINDOW = timedelta(days=7)
ACTIONS = ("favorite", "bin", "drag", "link", "more")
ACTION_TIPS = {
    "favorite": "Add to favorites",
    "bin": "Add to a sorting bin",
    "drag": "Drag into your editor, or click to reveal in Finder",
    "link": "Copy source link",
    "more": "More actions",
}


# ---------------------------------------------------------------- formatting
def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def format_bpm(bpm: float | None) -> str:
    return f"{int(round(bpm))} BPM" if bpm else "—"


def format_uses(count: int | None) -> str:
    if count is None:
        return "—"
    if count >= 1_000_000:
        text = f"{count / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{text}M"
    if count >= 1_000:
        return f"{round(count / 1_000)}K"
    return str(count)


def is_new(added_at: str, now: datetime | None = None) -> bool:
    if not added_at:
        return False
    try:
        added = datetime.fromisoformat(added_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if added.tzinfo is None:
        added = added.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    return timedelta(0) <= now - added <= NEW_WINDOW


def tag_lines(tags: tuple[str, ...] | list[str]) -> tuple[str, str]:
    tags = [t for t in tags if t]
    return ", ".join(tags[:2]), ", ".join(tags[2:4])


def initials(title: str) -> str:
    words = [w for w in (title or "").split() if w[:1].isalnum()]
    return "".join(w[0] for w in words[:2]).upper() or "♪"


def tile_color(music_id: str) -> QColor:
    """Stable tile color per sound, so a sound without artwork always looks the same."""
    digest = hashlib.blake2s(str(music_id).encode("utf-8"), digest_size=2).digest()
    return TILES[int.from_bytes(digest, "big") % len(TILES)]


def accessible_row_text(record) -> str:
    parts = [record.title or record.music_id]
    if record.artist:
        parts.append(record.artist)
    parts.append(format_duration(record.duration_seconds))
    if record.bpm:
        parts.append(format_bpm(record.bpm))
    if record.usage_count is not None:
        parts.append(f"{format_uses(record.usage_count)} uses")
    return ", ".join(parts)


def resample_peaks(peaks: list[float], bars: int) -> list[float]:
    """Max-pool ``peaks`` down (or repeat up) to ``bars`` values."""
    if bars <= 0:
        return []
    if not peaks:
        return [0.0] * bars
    n = len(peaks)
    out = []
    for i in range(bars):
        a = int(i * n / bars)
        b = max(a + 1, int((i + 1) * n / bars))
        out.append(max(peaks[a:b]))
    return out


# ---------------------------------------------------------------- caches
class ArtworkCache:
    """Scaled artwork thumbnails, decoded at the target size (QImageReader scales
    during decode, so a 1000 px JPEG never lands in memory full size)."""

    def __init__(self, capacity: int = 800) -> None:
        self._items: OrderedDict[tuple[str, int], QPixmap | None] = OrderedDict()
        self._capacity = capacity

    def get(self, path: Path | None, size: int) -> QPixmap | None:
        if path is None:
            return None
        key = (str(path), size)
        if key in self._items:
            self._items.move_to_end(key)
            return self._items[key]
        pixmap = None
        reader = QImageReader(str(path))
        if reader.canRead():
            original = reader.size()
            if original.isValid() and original.width() > 0 and original.height() > 0:
                scale = size / min(original.width(), original.height())
                reader.setScaledSize(QSize(max(1, round(original.width() * scale)), max(1, round(original.height() * scale))))
            image = reader.read()
            if not image.isNull():
                pixmap = QPixmap.fromImage(image)
        self._items[key] = pixmap
        if len(self._items) > self._capacity:
            self._items.popitem(last=False)
        return pixmap


class PeaksCache:
    def __init__(self, capacity: int = 4000) -> None:
        self._items: OrderedDict[str, list[float] | None] = OrderedDict()
        self._capacity = capacity

    def get(self, path: Path | None) -> list[float] | None:
        if path is None:
            return None
        key = str(path)
        if key in self._items:
            self._items.move_to_end(key)
            return self._items[key]
        peaks = load_peaks(path)
        if peaks is None:
            return None  # don't cache misses: the backfill may write the file any moment
        self._items[key] = peaks
        if len(self._items) > self._capacity:
            self._items.popitem(last=False)
        return peaks

    def invalidate(self, path: Path | None = None) -> None:
        if path is None:
            self._items.clear()
        else:
            self._items.pop(str(path), None)


# ---------------------------------------------------------------- layout
@dataclass(frozen=True)
class RowLayout:
    art: QRect
    play: QRect
    text: QRect
    icons: QRect | None
    wave: QRect | None
    length: QRect | None
    tags: QRect | None
    uses: QRect | None
    actions: dict[str, QRect]

    def visible_rects(self) -> list[QRect]:
        rects = [self.art, self.play, self.text]
        rects += [r for r in (self.icons, self.wave, self.length, self.tags, self.uses) if r is not None]
        rects += list(self.actions.values())
        return rects


def row_layout(rect: QRect, density: str = "comfortable") -> RowLayout:
    """Place every cell of a row inside ``rect``.

    Breakpoints (row width, after the 16 px inner padding), chosen so the waveform keeps
    roughly 200 px+ before any optional column appears: tags need 1150, the full action
    strip 1000 (else favorite + more), uses 860; the waveform drops under 600, and the
    icons and length under 460. Cells never overlap and
    always stay inside ``rect``.
    """
    pad, gap = 16, 14
    art_size = ART_SIZE.get(density, 56)
    inner = rect.adjusted(pad, 0, -pad, 0)
    width = inner.width()

    def box(x: int, w: int, h: int | None = None) -> QRect:
        h = inner.height() if h is None else min(h, inner.height())
        # Centre from the top edge: QRect.center() rounds down and would put a
        # full-height cell 1 px above the row.
        return QRect(x, inner.top() + (inner.height() - h) // 2, max(0, w), h)

    x = inner.left()
    art = box(x, art_size, art_size)
    x += art_size + gap
    play = box(x, 44, 44)
    x += 44 + gap

    # Full action strip only on wide rows; below that the waveform matters more
    # (everything is still in the ⋮ menu).
    actions_order = ACTIONS if width >= 1000 else ("favorite", "more")
    actions_w = 40 * len(actions_order)
    right = inner.right() + 1
    actions: dict[str, QRect] = {}
    ax = right - actions_w
    for name in actions_order:
        actions[name] = box(ax, 40, 44)
        ax += 40
    right -= actions_w + gap

    uses = tags = length = wave = icons = None
    if width >= 860:
        uses = box(right - 64, 64)
        right -= 64 + gap
    available = right - x
    if width >= 1150:
        tags_w = max(140, int(available * 0.16))
        tags = box(right - tags_w, tags_w)
        right -= tags_w + gap
    if width >= 460:
        length = box(right - 72, 72)
        right -= 72 + gap
    if width >= 460:
        icons_w = 48
    else:
        icons_w = 0
    available = right - x
    if width >= 600:
        text_w = max(170, min(420, int(available * 0.36)))
        text = box(x, text_w)
        x += text_w + gap
        if icons_w:
            icons = box(x, icons_w)
            x += icons_w + gap
        wave = box(x, max(0, right - x), 40)
        if wave.width() < 80:
            wave = None
    else:
        if icons_w:
            icons = box(right - icons_w, icons_w)
            right -= icons_w + gap
        text = box(x, max(60, right - x))
    return RowLayout(art, play, text, icons, wave, length, tags, uses, actions)


# ---------------------------------------------------------------- icons
def _pen(color: QColor, width: float = 1.8) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def draw_icon(painter: QPainter, name: str, rect: QRect | QRectF, color: QColor, *, filled: bool = False, size: float = 18.0) -> None:
    """Stroke icons from the design boards, drawn in a ``size``-px box centred in ``rect``."""
    r = QRectF(rect)
    s = size / 24.0
    ox = r.center().x() - size / 2
    oy = r.center().y() - size / 2

    def p(x: float, y: float) -> QPointF:
        return QPointF(ox + x * s, oy + y * s)

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(_pen(color, 2.0 * s * 0.9 + 0.2))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    if name == "play":
        path.moveTo(p(7, 4.5))
        path.lineTo(p(19.5, 12))
        path.lineTo(p(7, 19.5))
        path.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
    elif name == "pause":
        path.addRoundedRect(QRectF(p(6, 4), p(10, 20)), 1 * s, 1 * s)
        path.addRoundedRect(QRectF(p(14, 4), p(18, 20)), 1 * s, 1 * s)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
    elif name in ("favorite", "heart"):
        path.moveTo(p(12, 21))
        path.cubicTo(p(5, 15), p(2.5, 12), p(2.5, 8.5))
        path.cubicTo(p(2.5, 5.5), p(5, 3.5), p(7.5, 3.5))
        path.cubicTo(p(9.5, 3.5), p(11, 4.6), p(12, 6))
        path.cubicTo(p(13, 4.6), p(14.5, 3.5), p(16.5, 3.5))
        path.cubicTo(p(19, 3.5), p(21.5, 5.5), p(21.5, 8.5))
        path.cubicTo(p(21.5, 12), p(19, 15), p(12, 21))
        if filled:
            painter.setBrush(color)
    elif name == "bin":
        path.moveTo(p(12, 5))
        path.lineTo(p(12, 19))
        path.moveTo(p(5, 12))
        path.lineTo(p(19, 12))
    elif name == "drag":
        path.moveTo(p(12, 3))
        path.lineTo(p(12, 15))
        path.moveTo(p(7, 10))
        path.lineTo(p(12, 15))
        path.lineTo(p(17, 10))
        path.moveTo(p(5, 21))
        path.lineTo(p(19, 21))
    elif name == "link":
        path.addRoundedRect(QRectF(p(2.5, 9), p(12.5, 15)), 3 * s, 3 * s)
        path.addRoundedRect(QRectF(p(11.5, 9), p(21.5, 15)), 3 * s, 3 * s)
    elif name == "more":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        for y in (5, 12, 19):
            path.addEllipse(p(12, y), 1.8 * s, 1.8 * s)
    elif name in ("prev", "next"):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        if name == "next":
            path.moveTo(p(4, 5.5))
            path.lineTo(p(15, 12))
            path.lineTo(p(4, 18.5))
            path.closeSubpath()
            path.addRect(QRectF(p(16, 5), p(18.5, 19)))
        else:
            path.moveTo(p(20, 5.5))
            path.lineTo(p(9, 12))
            path.lineTo(p(20, 18.5))
            path.closeSubpath()
            path.addRect(QRectF(p(5.5, 5), p(8, 19)))
    elif name == "phone":
        path.addRoundedRect(QRectF(p(6, 2), p(18, 22)), 3 * s, 3 * s)
        path.moveTo(p(11, 18))
        path.lineTo(p(13, 18))
    elif name == "note":
        path.moveTo(p(9, 18))
        path.lineTo(p(9, 5))
        path.lineTo(p(21, 3))
        path.lineTo(p(21, 16))
        path.addEllipse(p(6, 18), 3 * s, 3 * s)
        path.addEllipse(p(18, 16), 3 * s, 3 * s)
    elif name == "mic":
        path.addRoundedRect(QRectF(p(9, 2), p(15, 14)), 3 * s, 3 * s)
        path.moveTo(p(5, 10))
        path.cubicTo(p(5, 18), p(19, 18), p(19, 10))
        path.moveTo(p(12, 17))
        path.lineTo(p(12, 22))
    elif name == "shuffle":
        path.moveTo(p(3, 7))
        path.lineTo(p(7, 7))
        path.cubicTo(p(12, 7), p(12, 17), p(17, 17))
        path.lineTo(p(21, 17))
        path.moveTo(p(3, 17))
        path.lineTo(p(7, 17))
        path.cubicTo(p(12, 17), p(12, 7), p(17, 7))
        path.lineTo(p(21, 7))
        for y in (7, 17):  # arrowheads
            path.moveTo(p(18, y - 3))
            path.lineTo(p(21, y))
            path.lineTo(p(18, y + 3))
    elif name == "repeat":
        path.moveTo(p(4, 12))
        path.lineTo(p(4, 10))
        path.cubicTo(p(4, 8), p(5.5, 6.5), p(7.5, 6.5))
        path.lineTo(p(20, 6.5))
        path.moveTo(p(17, 3.5))
        path.lineTo(p(20, 6.5))
        path.lineTo(p(17, 9.5))
        path.moveTo(p(20, 12))
        path.lineTo(p(20, 14))
        path.cubicTo(p(20, 16), p(18.5, 17.5), p(16.5, 17.5))
        path.lineTo(p(4, 17.5))
        path.moveTo(p(7, 14.5))
        path.lineTo(p(4, 17.5))
        path.lineTo(p(7, 20.5))
    elif name == "video":
        path.addRoundedRect(QRectF(p(2, 5), p(16, 19)), 2 * s, 2 * s)
        path.moveTo(p(16, 10))
        path.lineTo(p(22, 6))
        path.lineTo(p(22, 18))
        path.lineTo(p(16, 14))
    painter.drawPath(path)
    painter.restore()


# ---------------------------------------------------------------- host protocol
class TrackListHost(Protocol):
    def track_is_playing(self, music_id: str) -> bool: ...
    def track_progress(self, music_id: str) -> float: ...
    def track_play(self, music_id: str) -> None: ...
    def track_favorite(self, music_id: str) -> None: ...
    def track_add_to_bin(self, music_id: str, global_pos: QPoint) -> None: ...
    def track_reveal(self, music_id: str) -> None: ...
    def track_copy_link(self, music_id: str) -> None: ...
    def track_more(self, global_pos: QPoint) -> None: ...
    def track_seek(self, music_id: str, fraction: float) -> None: ...


# ---------------------------------------------------------------- delegate
class TrackRowDelegate(QStyledItemDelegate):
    """Paints one sound per row and routes clicks on its controls to the host."""

    def __init__(self, host: TrackListHost, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.host = host
        self.density = "comfortable"
        self.artwork = ArtworkCache()
        self.peaks = PeaksCache()
        self.now: Callable[[], datetime] = lambda: datetime.now(UTC)
        self.title_font = QFont("Quicksand", 11)
        self.title_font.setPixelSize(15)
        self.title_font.setWeight(QFont.Weight.Bold)
        self.body_font = QFont("Quicksand")
        self.body_font.setPixelSize(13)
        self.meta_font = QFont("Quicksand")
        self.meta_font.setPixelSize(12)
        self.meta_font.setWeight(QFont.Weight.DemiBold)
        self.badge_font = QFont("Quicksand")
        self.badge_font.setPixelSize(11)
        self.badge_font.setWeight(QFont.Weight.Bold)
        self.badge_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 106)
        self.art_font = QFont("Unbounded")
        self.art_font.setWeight(QFont.Weight.Bold)

    # sizing -------------------------------------------------------------------
    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt override
        return QSize(max(option.rect.width(), 320), ROW_HEIGHT.get(self.density, 76))

    def layout_for(self, rect: QRect) -> RowLayout:
        return row_layout(rect, self.density)

    # painting -----------------------------------------------------------------
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # noqa: D102
        record = index.data(RECORD_ROLE)
        if record is None:
            return super().paint(painter, option, index)
        music_id = record.music_id
        playing = self.host.track_is_playing(music_id)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        focused = bool(option.state & QStyle.StateFlag.State_HasFocus)
        playable = bool(index.data(PLAYABLE_ROLE))
        favorite = bool(index.data(FAVORITE_ROLE))
        rect = option.rect.adjusted(0, 2, 0, -2)
        lay = self.layout_for(option.rect)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        # Row ground: transparent → hover → selected/playing (raised + lilac ring).
        bg = QPainterPath()
        bg.addRoundedRect(QRectF(rect), 14, 14)
        if selected or playing:
            painter.fillPath(bg, RAISED)
            painter.setPen(QPen(QColor(183, 147, 255, 130 if selected else 90), 1))
            painter.drawPath(bg)
        elif hovered:
            painter.fillPath(bg, NIGHT_2)
        if focused and selected:
            painter.setPen(QPen(CYAN, 2))
            painter.drawRoundedRect(QRectF(rect).adjusted(1, 1, -1, -1), 13, 13)

        dim = not playable
        if dim:
            painter.setOpacity(0.62)
        self._paint_art(painter, lay.art, record)
        draw_icon(painter, "pause" if playing else "play", lay.play, WAVE_OFF if dim else INK, size=18)
        self._paint_text(painter, lay.text, record)
        if lay.icons is not None:
            self._paint_icons(painter, lay.icons, record)
        if lay.wave is not None:
            self._paint_wave(painter, lay.wave, record, playable, self.host.track_progress(music_id) if playing else 0.0)
        if lay.length is not None:
            self._two_lines(painter, lay.length, format_duration(record.duration_seconds), format_bpm(record.bpm), first_font=self.body_font, align=Qt.AlignmentFlag.AlignLeft, tabular=True)
        if lay.tags is not None:
            first, second = tag_lines(record.tags)
            self._two_lines(painter, lay.tags, first or "—", second, first_font=self.body_font, align=Qt.AlignmentFlag.AlignLeft)
        if lay.uses is not None:
            self._two_lines(painter, lay.uses, format_uses(record.usage_count), "uses" if record.usage_count is not None else "", first_font=self._bold(self.body_font, 14), align=Qt.AlignmentFlag.AlignLeft, tabular=True)
        painter.setOpacity(1.0)
        show_actions = hovered or selected or playing or favorite
        for name, r in lay.actions.items():
            if name == "favorite":
                color = PINK if favorite else (INK if show_actions else WAVE_OFF)
                draw_icon(painter, "favorite", r, color, filled=favorite)
            else:
                draw_icon(painter, name, r, INK if show_actions else WAVE_OFF)
        painter.restore()

    @staticmethod
    def _bold(font: QFont, px: int) -> QFont:
        f = QFont(font)
        f.setPixelSize(px)
        f.setWeight(QFont.Weight.Bold)
        return f

    def _paint_art(self, painter: QPainter, r: QRect, record) -> None:
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(r), 10, 10)
        pixmap = self.artwork.get(record.artwork_path, r.width() * 2)
        painter.save()
        painter.setClipPath(clip)
        if pixmap is not None:
            # Centre-crop to a square.
            src = pixmap.rect()
            side = min(src.width(), src.height())
            painter.drawPixmap(r, pixmap, QRect((src.width() - side) // 2, (src.height() - side) // 2, side, side))
        else:
            painter.fillRect(r, tile_color(record.music_id))
            f = QFont(self.art_font)
            f.setPixelSize(max(10, round(r.width() / 3.2)))
            painter.setFont(f)
            painter.setPen(QColor(251, 237, 255, 215))
            painter.drawText(r, Qt.AlignmentFlag.AlignCenter, initials(record.title))
        painter.restore()
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
        painter.drawPath(clip)

    def _paint_text(self, painter: QPainter, r: QRect, record) -> None:
        title = record.title or record.music_id
        new = is_new(record.added_at, self.now())
        fm = QFontMetrics(self.title_font)
        badge_w = QFontMetrics(self.badge_font).horizontalAdvance("NEW") + 8 if new else 0
        top = QRect(r.left(), r.center().y() - 21, r.width(), 22)
        bottom = QRect(r.left(), r.center().y() + 1, r.width(), 20)
        painter.setFont(self.title_font)
        painter.setPen(INK)
        title_text = fm.elidedText(title, Qt.TextElideMode.ElideRight, max(0, r.width() - badge_w))
        painter.drawText(top, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), title_text)
        if new:
            x = r.left() + fm.horizontalAdvance(title_text) + 8
            painter.setFont(self.badge_font)
            painter.setPen(GOLD)
            painter.drawText(QRect(x, top.top(), badge_w, top.height()), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), "NEW")
        painter.setFont(self.body_font)
        painter.setPen(MUTED)
        artist = QFontMetrics(self.body_font).elidedText(record.artist or "", Qt.TextElideMode.ElideRight, r.width())
        painter.drawText(bottom, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), artist)

    def _paint_icons(self, painter: QPainter, r: QRect, record) -> None:
        x = r.left()
        if (record.transcript_text or "").strip():
            draw_icon(painter, "mic", QRect(x, r.top(), 20, r.height()), MUTED, size=16)
        x += 24
        if record.associated_video_count > 0:
            draw_icon(painter, "video", QRect(x, r.top(), 20, r.height()), MUTED, size=16)

    def _paint_wave(self, painter: QPainter, r: QRect, record, playable: bool, progress: float) -> None:
        if not playable:
            pen = QPen(WAVE_OFF, 2, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(r.left(), r.center().y(), r.right(), r.center().y())
            return
        peaks = self.peaks.get(record.waveform_path)
        bar_w, bar_gap = 2, 1
        bars = max(1, r.width() // (bar_w + bar_gap))
        values = resample_peaks(peaks, bars) if peaks else None
        played_bars = int(bars * max(0.0, min(1.0, progress)))
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(bars):
            if values is None:
                h = 2  # not analysed yet: a quiet flat line, never a fake shape
            else:
                h = max(3, int(round(values[i] * r.height())))
            painter.setBrush(QBrush(LILAC if i < played_bars else WAVE_OFF))
            painter.drawRoundedRect(QRectF(r.left() + i * (bar_w + bar_gap), r.center().y() - h / 2, bar_w, h), 1, 1)

    def _two_lines(self, painter: QPainter, r: QRect, first: str, second: str, *, first_font: QFont, align, tabular: bool = False) -> None:
        f1 = QFont(first_font)
        f2 = QFont(self.meta_font)
        if tabular:
            for f in (f1, f2):
                f.setFeature(QFont.Tag("tnum"), 1)
        top = QRect(r.left(), r.center().y() - 20, r.width(), 20)
        bottom = QRect(r.left(), r.center().y() + 1, r.width(), 18)
        painter.setFont(f1)
        painter.setPen(INK)
        painter.drawText(top, int(align | Qt.AlignmentFlag.AlignVCenter), QFontMetrics(f1).elidedText(first, Qt.TextElideMode.ElideRight, r.width()))
        if second:
            painter.setFont(f2)
            painter.setPen(MUTED)
            painter.drawText(bottom, int(align | Qt.AlignmentFlag.AlignVCenter), QFontMetrics(f2).elidedText(second, Qt.TextElideMode.ElideRight, r.width()))

    # interaction --------------------------------------------------------------
    def hit_test(self, rect: QRect, pos: QPoint) -> str | None:
        lay = self.layout_for(rect)
        for name, r in lay.actions.items():
            if r.contains(pos):
                return name
        if lay.play.contains(pos):
            return "play"
        if lay.wave is not None and lay.wave.adjusted(0, -12, 0, 12).contains(pos):
            return "wave"
        return None

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802 - Qt override
        if event.type() not in (QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonPress):
            return super().editorEvent(event, model, option, index)
        if event.button() != Qt.MouseButton.LeftButton:
            return super().editorEvent(event, model, option, index)
        record = index.data(RECORD_ROLE)
        if record is None:
            return False
        pos = event.position().toPoint()
        hit = self.hit_test(option.rect, pos)
        if hit is None:
            return super().editorEvent(event, model, option, index)
        if event.type() == QEvent.Type.MouseButtonPress:
            return hit != "drag"  # let a press on the drag handle start a drag
        mid = record.music_id
        widget = option.widget
        global_pos = widget.mapToGlobal(pos) if widget is not None else pos
        if hit == "play":
            self.host.track_play(mid)
        elif hit == "favorite":
            self.host.track_favorite(mid)
        elif hit == "bin":
            self.host.track_add_to_bin(mid, global_pos)
        elif hit == "drag":
            self.host.track_reveal(mid)
        elif hit == "link":
            self.host.track_copy_link(mid)
        elif hit == "more":
            self.host.track_more(global_pos)
        elif hit == "wave":
            lay = self.layout_for(option.rect)
            fraction = (pos.x() - lay.wave.left()) / max(1, lay.wave.width())
            if self.host.track_is_playing(mid):
                self.host.track_seek(mid, max(0.0, min(1.0, fraction)))
            else:
                self.host.track_play(mid)
        return True

    def helpEvent(self, event, view, option, index) -> bool:  # noqa: N802 - Qt override
        hit = self.hit_test(option.rect, event.pos())
        record = index.data(RECORD_ROLE)
        tip = None
        if hit in ACTION_TIPS:
            tip = ACTION_TIPS[hit]
            if hit == "favorite" and bool(index.data(FAVORITE_ROLE)):
                tip = "Remove from favorites"
        elif hit == "play" and record is not None:
            tip = ("Pause " if self.host.track_is_playing(record.music_id) else "Play ") + (record.title or "")
        elif hit == "wave":
            tip = "Click to seek" if record is not None and self.host.track_is_playing(record.music_id) else "Click to play"
        if tip:
            QToolTip.showText(event.globalPos(), tip, view)
            return True
        return super().helpEvent(event, view, option, index)


# ---------------------------------------------------------------- view
class TrackListView(QListView):
    """List view over the library model; shares the table's selection model."""

    playRequested = Signal()
    favoriteRequested = Signal()
    binRequested = Signal()

    def __init__(self, delegate: TrackRowDelegate, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("trackList")
        self.setItemDelegate(delegate)
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSpacing(0)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.viewport().setAutoFillBackground(False)
        self.setAccessibleName("Sounds")

    def row_delegate(self) -> TrackRowDelegate:
        return self.itemDelegate()

    def set_density(self, density: str) -> None:
        self.row_delegate().density = density if density in ROW_HEIGHT else "comfortable"
        self.scheduleDelayedItemsLayout()
        self.viewport().update()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        key = event.key()
        if key == Qt.Key.Key_Space and not event.modifiers():
            self.playRequested.emit()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.playRequested.emit()
            return
        if key == Qt.Key.Key_F and not event.modifiers():
            self.favoriteRequested.emit()
            return
        if key == Qt.Key.Key_B and not event.modifiers():
            self.binRequested.emit()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self.scheduleDelayedItemsLayout()  # rows re-flow their columns at the new width


# ---------------------------------------------------------------- waveform seek bar
class WaveformBar(QWidget):
    """A waveform you can click or drag to seek (detail pane / now playing)."""

    seekRequested = Signal(float)  # fraction 0..1

    def __init__(self, parent: QWidget | None = None, *, bar_height: int = 44) -> None:
        super().__init__(parent)
        self._peaks: list[float] | None = None
        self._progress = 0.0
        self._enabled_audio = False
        self.setMinimumHeight(bar_height)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Seek")

    def set_peaks(self, peaks: list[float] | None, *, playable: bool = True) -> None:
        self._peaks = peaks
        self._enabled_audio = playable
        self.update()

    def set_progress(self, fraction: float) -> None:
        fraction = max(0.0, min(1.0, float(fraction)))
        if abs(fraction - self._progress) > 0.001:
            self._progress = fraction
            self.update()

    def progress(self) -> float:
        return self._progress

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(320, self.minimumHeight())

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(0, 4, 0, -4)
        if not self._enabled_audio:
            painter.setPen(QPen(WAVE_OFF, 2, Qt.PenStyle.DashLine))
            painter.drawLine(r.left(), r.center().y(), r.right(), r.center().y())
            return
        bars = max(1, r.width() // 3)
        values = resample_peaks(self._peaks, bars) if self._peaks else None
        played = int(bars * self._progress)
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(bars):
            h = 2 if values is None else max(3, int(round(values[i] * r.height())))
            painter.setBrush(LILAC if i < played else WAVE_OFF)
            painter.drawRoundedRect(QRectF(r.left() + i * 3, r.center().y() - h / 2, 2, h), 1, 1)
        if self.hasFocus():
            painter.setPen(QPen(CYAN, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)

    def _seek_to(self, x: float) -> None:
        if not self._enabled_audio:
            return
        fraction = max(0.0, min(1.0, x / max(1, self.width())))
        self.set_progress(fraction)
        self.seekRequested.emit(fraction)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._seek_to(event.position().x())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._seek_to(event.position().x())

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        step = 0.05
        if event.key() == Qt.Key.Key_Left:
            self._seek_to((self._progress - step) * self.width())
        elif event.key() == Qt.Key.Key_Right:
            self._seek_to((self._progress + step) * self.width())
        else:
            super().keyPressEvent(event)


# ---------------------------------------------------------------- flow layout
class FlowLayout(QLayout):
    """Lays widgets out left to right and wraps onto new lines when they don't fit
    (the filter chips). Nothing is ever squeezed below its size hint or overlapped;
    the layout grows taller instead."""

    def __init__(self, parent: QWidget | None = None, *, spacing: int = 8) -> None:
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802 - Qt override
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _do_layout(self, rect: QRect, *, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        # Break into lines first, then place each line with its items centred vertically
        # (a checkbox or label beside a taller combo would otherwise hug the top).
        lines: list[list] = [[]]
        x = area.x()
        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            width = item.sizeHint().width()
            if lines[-1] and x + width > area.right() + 1:
                lines.append([])
                x = area.x()
            lines[-1].append(item)
            x += width + self._spacing
        y = area.y()
        for line in lines:
            if not line:
                continue
            height = max(item.sizeHint().height() for item in line)
            x = area.x()
            for item in line:
                hint = item.sizeHint()
                if apply:
                    item.setGeometry(QRect(QPoint(x, y + (height - hint.height()) // 2), hint))
                x += hint.width() + self._spacing
            y += height + self._spacing
        return max(0, y - self._spacing - rect.y()) + margins.bottom()


# ---------------------------------------------------------------- now-playing bar
class IconButton(QToolButton):
    """A 44×44 button that paints one of the design's stroke icons."""

    def __init__(self, icon: str, label: str, parent: QWidget | None = None, *, filled_disc: bool = False) -> None:
        super().__init__(parent)
        self.icon_name = icon
        self.filled_disc = filled_disc
        self.setAccessibleName(label)
        self.setToolTip(label)
        self.setFixedSize(QSize(48, 48) if filled_disc else QSize(44, 44))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_icon_name(self, icon: str, label: str) -> None:
        self.icon_name = icon
        self.setAccessibleName(label)
        self.setToolTip(label)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self.filled_disc:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(INK)
            painter.drawEllipse(r.adjusted(1, 1, -1, -1))
            color = NIGHT
        else:
            if self.underMouse():
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 255, 255, 20))
                painter.drawEllipse(r.adjusted(2, 2, -2, -2))
            color = INK if self.isEnabled() else WAVE_OFF
            if self.isCheckable() and self.isChecked():
                color = CYAN
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(CYAN)
                painter.drawEllipse(QPointF(r.center().x(), r.bottom() - 6), 2, 2)
        if self.hasFocus():
            painter.setPen(QPen(CYAN, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(r.adjusted(1, 1, -1, -1))
        draw_icon(painter, self.icon_name, self.rect(), color, size=18)


class ElidedLabel(QLabel):
    """A one-line label that ends in "…" instead of clipping mid-glyph."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt override
        self._full = text or ""
        self.setAccessibleName(self._full)
        self._refresh()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        elided = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(0, self.width()))
        super().setText(elided)
        self.setToolTip(self._full if elided != self._full else "")


class NowPlayingBar(QFrame):
    """Bottom bar (design board 1): art, title/artist, previous / play / next, a
    waveform scrubber with elapsed and total time, then repeat (continuous play) and
    shuffle. It is the app's only transport, so it stays visible; with nothing
    loaded it shows a hint and Play acts on the selected row."""

    EMPTY_TITLE = "Nothing playing"
    EMPTY_HINT = "Select a sound and press Space"

    previousRequested = Signal()
    toggleRequested = Signal()
    nextRequested = Signal()
    seekRequested = Signal(float)
    shuffleRequested = Signal()
    repeatToggled = Signal(bool)

    def __init__(self, artwork: ArtworkCache, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("nowPlayingBar")
        self.setAccessibleName("Now playing")
        self._artwork = artwork
        self._record = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(14)
        self.art = QLabel()
        self.art.setFixedSize(44, 44)
        self.art.setObjectName("nowPlayingArt")
        layout.addWidget(self.art)
        text = QVBoxLayout()
        text.setSpacing(1)
        self.title = ElidedLabel("Nothing playing")
        self.title.setObjectName("nowPlayingTrack")
        self.artist = ElidedLabel("")
        self.artist.setObjectName("nowPlayingArtist")
        for label in (self.title, self.artist):
            label.setMinimumWidth(80)
        text.addWidget(self.title)
        text.addWidget(self.artist)
        text_box = QWidget()
        text_box.setObjectName("nowPlayingText")
        text_box.setLayout(text)
        text_box.setMinimumWidth(170)  # room for a real title before it elides
        text_box.setMaximumWidth(280)
        layout.addWidget(text_box, 1)
        self.prev_button = IconButton("prev", "Previous sound")
        self.play_button = IconButton("pause", "Pause", filled_disc=True)
        self.next_button = IconButton("next", "Next sound")
        self.prev_button.clicked.connect(self.previousRequested)
        self.play_button.clicked.connect(self.toggleRequested)
        self.next_button.clicked.connect(self.nextRequested)
        for button in (self.prev_button, self.play_button, self.next_button):
            layout.addWidget(button)
        self.elapsed = QLabel("0:00")
        self.elapsed.setObjectName("nowPlayingTime")
        self.wave = WaveformBar(bar_height=32)
        self.wave.setAccessibleName("Seek in the playing sound")
        self.wave.seekRequested.connect(self.seekRequested)
        self.total = QLabel("0:00")
        self.total.setObjectName("nowPlayingTime")
        layout.addWidget(self.elapsed)
        layout.addWidget(self.wave, 3)
        layout.addWidget(self.total)
        self.repeat_button = IconButton("repeat", "Continuous play: keep playing down the list")
        self.repeat_button.setCheckable(True)
        self.repeat_button.toggled.connect(self.repeatToggled)
        self.repeat_button.toggled.connect(lambda _on: self.repeat_button.update())
        self.shuffle_button = IconButton("shuffle", "Play a random sound from the list")
        self.shuffle_button.clicked.connect(self.shuffleRequested)
        layout.addWidget(self.repeat_button)
        layout.addWidget(self.shuffle_button)
        self._show_empty()

    COMPACT_BELOW = 720  # px: under this the scrubber and times give way to the title

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        wide = self.width() >= self.COMPACT_BELOW
        for widget in (self.elapsed, self.wave, self.total):
            if widget.isVisibleTo(self) != wide:
                widget.setVisible(wide)

    def _show_empty(self) -> None:
        self._shown_id = None
        self.title.setText(self.EMPTY_TITLE)
        self.artist.setText(self.EMPTY_HINT)
        pixmap = QPixmap(88, 88)
        pixmap.fill(RAISED)
        painter = QPainter(pixmap)
        draw_icon(painter, "note", pixmap.rect(), MUTED, size=40)
        painter.end()
        self.art.setPixmap(pixmap.scaled(44, 44, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.wave.set_peaks(None, playable=False)
        self.wave.set_progress(0.0)
        self.elapsed.setText("0:00")
        self.total.setText("0:00")
        self.set_playing(False)

    def set_track(self, record, peaks: list[float] | None, *, playable: bool) -> None:
        if record is None:
            self._record = None
            self._show_empty()
            return
        self._record = record
        if getattr(self, "_shown_id", None) != record.music_id:
            self.wave.set_progress(0.0)
            self.elapsed.setText("0:00")
        self._shown_id = record.music_id
        self.title.setText(record.title or record.music_id)
        self.artist.setText(record.artist or "")
        pixmap = self._artwork.get(record.artwork_path, 88)
        if pixmap is None:
            pixmap = QPixmap(88, 88)
            pixmap.fill(tile_color(record.music_id))
            painter = QPainter(pixmap)
            f = QFont("Unbounded")
            f.setPixelSize(28)
            f.setWeight(QFont.Weight.Bold)
            painter.setFont(f)
            painter.setPen(INK)
            painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, initials(record.title))
            painter.end()
        side = min(pixmap.width(), pixmap.height())
        square = pixmap.copy((pixmap.width() - side) // 2, (pixmap.height() - side) // 2, side, side)
        self.art.setPixmap(square.scaled(44, 44, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.wave.set_peaks(peaks, playable=playable)
        self.total.setText(format_duration(record.duration_seconds))

    def record(self):
        return self._record

    def set_playing(self, playing: bool) -> None:
        self.play_button.set_icon_name("pause" if playing else "play", "Pause" if playing else "Play")

    def set_progress(self, fraction: float, elapsed: str, total: str | None = None) -> None:
        self.wave.set_progress(fraction)
        self.elapsed.setText(elapsed)
        if total:
            self.total.setText(total)


class StrokeIcon(QWidget):
    """A fixed-size painted icon from :func:`draw_icon` (replaces emoji glyphs)."""

    def __init__(self, icon: str, size: int = 24, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.icon_name = icon
        self.color = MUTED
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def set_color(self, color: QColor) -> None:
        self.color = QColor(color)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        draw_icon(painter, self.icon_name, self.rect(), self.color, size=float(self.width()))
