"""The playback playhead must track the whole bar — especially at the right end.

Qt insets a slider handle's travel by half a handle at each end, so with the default
`margin: -5px 0` the playhead's centre only spanned ~1.3%..98.3% of the bar: it never
reached either end, and the error was worst on the RIGHT. In time terms that scales with
the track — on a 5.5-minute sound ~1.8% is about 6 seconds of visible error right where
the track ends, which is what makes it obvious on long playbacks.

The fix is a negative HORIZONTAL handle margin (half the handle + borders) so the handle
may overhang the groove, plus a click->value mapping that uses Qt's own geometry instead
of a naive `x / width()`.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSlider

import sound_vault.ui.desktop as desktop_module
from sound_vault.ui.desktop import SeekSlider

LONG_TRACK_MS = 330_470  # a real 5.5-minute vault sound
WIDTH = 600


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _slider() -> SeekSlider:
    _app()
    slider = SeekSlider(Qt.Orientation.Horizontal)
    slider.resize(WIDTH, 18)
    slider.setRange(0, LONG_TRACK_MS)
    slider.setStyleSheet(desktop_module.STYLESHEET)  # the app's real skin — the margin lives here
    slider.style().unpolish(slider)
    slider.style().polish(slider)
    return slider


def _handle_center_x(slider: SeekSlider) -> int:
    opt = QStyleOptionSlider()
    slider.initStyleOption(opt)
    return slider.style().subControlRect(
        QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, slider
    ).center().x()


def test_handle_is_never_clipped_by_the_widget_edges():
    """The handle must stay INSIDE the widget at both extremes.

    A negative horizontal handle margin lets the handle's centre reach the very ends, but
    Qt clips children to the widget rect — the handle then paints as a half-circle at 0%
    and 100%, i.e. visibly broken exactly at the ends. Guard against reintroducing that.
    """
    slider = _slider()
    for value in (0, LONG_TRACK_MS):
        slider.setValue(value)
        opt = QStyleOptionSlider()
        slider.initStyleOption(opt)
        rect = slider.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, slider
        )
        assert rect.left() >= 0, f"handle clipped at the left edge (x={rect.left()}) for value {value}"
        assert rect.right() <= WIDTH, f"handle clipped at the right edge (x={rect.right()}) for value {value}"


def test_playhead_moves_monotonically_and_reaches_the_extremes():
    slider = _slider()
    seen = []
    for fraction in (0.0, 0.25, 0.5, 0.75, 0.9, 1.0):
        slider.setValue(int(LONG_TRACK_MS * fraction))
        seen.append(_handle_center_x(slider))
    assert seen == sorted(seen), f"playhead must advance monotonically, got {seen}"
    assert seen[0] < WIDTH * 0.05, "playhead should start at the left end"
    assert seen[-1] > WIDTH * 0.95, "playhead should finish at the right end"


def test_click_maps_to_the_value_drawn_under_the_cursor():
    """Clicking the bar must seek to where the playhead visibly lands (round-trip)."""
    slider = _slider()
    for x in (0, 150, 300, 450, 560, WIDTH):
        value = slider._value_at_x(float(x))
        assert 0 <= value <= LONG_TRACK_MS
        slider.setValue(value)
        drawn = _handle_center_x(slider)
        assert abs(drawn - x) <= 10, f"click at x={x} drew the playhead at {drawn}"


def test_click_at_the_far_right_reaches_the_end_of_the_track():
    slider = _slider()
    assert slider._value_at_x(float(WIDTH)) == LONG_TRACK_MS
    assert slider._value_at_x(0.0) == 0
