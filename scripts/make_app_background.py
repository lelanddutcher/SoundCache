#!/usr/bin/env python3
"""Render the app's sparkle backgrounds using the brand's own sparkle motifs —
the 4-point stars + plus-twinkles from the hero icon's night background — scattered
over the violet-night gradient (+ a faint aurora on the deck).

Motifs lifted from `sound cache icon v2 hero.svg`:
  • 4-point star (filled): concave diamond, inner≈0.43·R — ink-white + cyan
  • plus twinkle (stroked): small cross — cyan + gold

Subtle by design (it sits behind a dense table). Outputs to
src/sound_vault/ui/assets/: deck-bg.png, sidebar-bg.png.
Run: python scripts/make_app_background.py   (needs PySide6)
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import (
    QImage, QPainter, QPainterPath, QPen, QLinearGradient, QRadialGradient, QColor, QBrush,
)
from PySide6.QtCore import QPointF, Qt

OUT = Path(__file__).resolve().parents[1] / "src" / "sound_vault" / "ui" / "assets"
INNER_RATIO = 0.43  # waist/tip ratio from the icon's 4-point star
# Weighted brand palette — white + cyan dominate (as in the icon), gold/lilac/pink accent.
STAR_COLORS = ["#fbedff", "#fbedff", "#fbedff", "#66ecff", "#66ecff", "#b793ff", "#ff6ad5"]
PLUS_COLORS = ["#66ecff", "#66ecff", "#ffd86b"]


def _night(w: int, h: int, stops: list[tuple[float, str]]) -> QImage:
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    g = QLinearGradient(0, 0, 0, h)
    for at, hexc in stops:
        g.setColorAt(at, QColor(hexc))
    p = QPainter(img)
    p.fillRect(0, 0, w, h, QBrush(g))
    p.end()
    return img


def _aurora(img: QImage, blobs: list[tuple[float, float, float, QColor]]) -> None:
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
    p.setPen(Qt.PenStyle.NoPen)
    for cx, cy, r, color in blobs:
        rg = QRadialGradient(QPointF(cx, cy), r)
        rg.setColorAt(0.0, color)
        edge = QColor(color)
        edge.setAlpha(0)
        rg.setColorAt(1.0, edge)
        p.setBrush(QBrush(rg))
        p.drawEllipse(QPointF(cx, cy), r, r)
    p.end()


def _star_path(cx: float, cy: float, r: float) -> QPainterPath:
    path = QPainterPath()
    for i in range(8):
        ang = math.radians(-90 + 45 * i)
        rad = r if i % 2 == 0 else r * INNER_RATIO
        x, y = cx + rad * math.cos(ang), cy + rad * math.sin(ang)
        path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
    path.closeSubpath()
    return path


def _sparkles(img: QImage, count: int, *, seed: int, max_alpha: int, max_r: float) -> None:
    rng = random.Random(seed)
    w, h = img.width(), img.height()
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    for _ in range(count):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        alpha = rng.randint(28, max_alpha)
        roll = rng.random()
        if roll < 0.30:  # plus twinkle (stroked cross)
            color = QColor(rng.choice(PLUS_COLORS))
            color.setAlpha(alpha)
            arm = rng.uniform(2.0, max_r * 0.6)
            pen = QPen(color, max(1.0, arm * 0.32))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(x - arm, y), QPointF(x + arm, y))
            p.drawLine(QPointF(x, y - arm), QPointF(x, y + arm))
            p.setPen(Qt.PenStyle.NoPen)
        else:  # 4-point star (filled)
            color = QColor(rng.choice(STAR_COLORS))
            color.setAlpha(alpha)
            r = rng.uniform(1.6, max_r)
            p.setBrush(QBrush(color))
            # a few bright ones get a soft halo
            if rng.random() < 0.10:
                halo = QColor(color)
                halo.setAlpha(max(14, alpha // 5))
                hg = QRadialGradient(QPointF(x, y), r * 4)
                hg.setColorAt(0.0, halo)
                clear = QColor(halo)
                clear.setAlpha(0)
                hg.setColorAt(1.0, clear)
                p.setBrush(QBrush(hg))
                p.drawEllipse(QPointF(x, y), r * 4, r * 4)
                p.setBrush(QBrush(color))
            p.drawPath(_star_path(x, y, r))
    p.end()


def main() -> int:
    app = QApplication.instance() or QApplication([])  # noqa: F841
    OUT.mkdir(parents=True, exist_ok=True)

    w, h = 2200, 1400
    deck = _night(w, h, [(0.0, "#1a0d40"), (0.05, "#150a33"), (1.0, "#0a0518")])
    _aurora(deck, [
        (w * 0.16, h * 0.10, w * 0.34, QColor(255, 106, 213, 24)),
        (w * 0.86, h * 0.16, w * 0.32, QColor(102, 236, 255, 20)),
        (w * 0.55, h * 0.04, w * 0.30, QColor(183, 147, 255, 20)),
    ])
    _sparkles(deck, 130, seed=7, max_alpha=210, max_r=8.5)
    deck.save(str(OUT / "deck-bg.png"))

    sw, sh = 760, 1400
    side = _night(sw, sh, [(0.0, "#1d1046"), (0.06, "#170c39"), (1.0, "#0a0518")])
    _sparkles(side, 40, seed=13, max_alpha=150, max_r=6.0)
    side.save(str(OUT / "sidebar-bg.png"))

    for f in ("deck-bg.png", "sidebar-bg.png"):
        print(f"  {f}: {(OUT / f).stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
