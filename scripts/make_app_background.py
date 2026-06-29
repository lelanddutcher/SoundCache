#!/usr/bin/env python3
"""Render the app's sparkle backgrounds — the brand's starry-night + aurora look
(from the website's .sky/.aurora) baked into the desktop surfaces.

Keeps it subtle: these sit behind a dense table, so stars are low-alpha and the
aurora is a faint glow. Outputs to src/sound_vault/ui/assets/:
  deck-bg.png     — main content deck (night gradient + stars + soft aurora)
  sidebar-bg.png  — sidebar (night gradient + fainter stars, no aurora)

Run: python scripts/make_app_background.py   (needs PySide6)
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter, QLinearGradient, QRadialGradient, QColor, QBrush
from PySide6.QtCore import QPointF, Qt

OUT = Path(__file__).resolve().parents[1] / "src" / "sound_vault" / "ui" / "assets"
STAR_TINTS = [QColor(255, 255, 255), QColor(215, 246, 255), QColor(255, 233, 251)]


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
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)  # additive glow
    for cx, cy, r, color in blobs:
        rg = QRadialGradient(QPointF(cx, cy), r)
        rg.setColorAt(0.0, color)
        edge = QColor(color)
        edge.setAlpha(0)
        rg.setColorAt(1.0, edge)
        p.setBrush(QBrush(rg))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), r, r)
    p.end()


def _stars(img: QImage, count: int, *, seed: int, max_alpha: int) -> None:
    rng = random.Random(seed)
    w, h = img.width(), img.height()
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    for _ in range(count):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        radius = rng.uniform(0.5, 1.9)
        tint = QColor(rng.choice(STAR_TINTS))
        tint.setAlpha(rng.randint(40, max_alpha))
        p.setBrush(QBrush(tint))
        p.drawEllipse(QPointF(x, y), radius, radius)
        # a few brighter stars get a soft halo + a tiny twinkle
        if rng.random() < 0.06:
            halo = QColor(tint)
            halo.setAlpha(max(20, tint.alpha() // 4))
            hg = QRadialGradient(QPointF(x, y), radius * 6)
            hg.setColorAt(0.0, halo)
            clear = QColor(halo)
            clear.setAlpha(0)
            hg.setColorAt(1.0, clear)
            p.setBrush(QBrush(hg))
            p.drawEllipse(QPointF(x, y), radius * 6, radius * 6)
            p.setBrush(QBrush(tint))
    p.end()


def main() -> int:
    app = QApplication.instance() or QApplication([])  # noqa: F841
    OUT.mkdir(parents=True, exist_ok=True)

    # --- main deck: gradient + stars + faint aurora (matches #mainDeck) ---
    w, h = 2200, 1400
    deck = _night(w, h, [(0.0, "#1a0d40"), (0.05, "#150a33"), (1.0, "#0a0518")])
    _aurora(deck, [
        (w * 0.16, h * 0.10, w * 0.34, QColor(255, 106, 213, 26)),  # pink, top-left
        (w * 0.86, h * 0.16, w * 0.32, QColor(102, 236, 255, 22)),  # cyan, top-right
        (w * 0.55, h * 0.04, w * 0.30, QColor(183, 147, 255, 22)),  # lilac, top-center
    ])
    _stars(deck, 240, seed=7, max_alpha=200)
    deck.save(str(OUT / "deck-bg.png"))

    # --- sidebar: its own gradient + fainter stars, no aurora ---
    sw, sh = 760, 1400
    side = _night(sw, sh, [(0.0, "#1d1046"), (0.06, "#170c39"), (1.0, "#0a0518")])
    _stars(side, 70, seed=13, max_alpha=150)
    side.save(str(OUT / "sidebar-bg.png"))

    for f in ("deck-bg.png", "sidebar-bg.png"):
        print(f"  {f}: {(OUT / f).stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
