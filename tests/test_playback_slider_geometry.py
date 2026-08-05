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
import re

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


def test_stylesheet_gives_the_handle_a_negative_horizontal_margin():
    """Guard the actual rule — a plain `margin: -5px 0` reintroduces the inset."""
    source = desktop_module.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    block = re.search(r"QSlider::handle:horizontal \{(.*?)\}", text, re.S)
    assert block, "slider handle style block not found"
    margin = re.search(r"margin:\s*-?\d+px\s+(-?\d+)px", block.group(1))
    assert margin, "handle margin must set an explicit horizontal value"
    assert int(margin.group(1)) < 0, "horizontal handle margin must be negative so the playhead reaches both ends"


def test_playhead_spans_the_full_bar_at_both_ends():
    slider = _slider()
    slider.setValue(0)
    left_pct = _handle_center_x(slider) / WIDTH * 100
    slider.setValue(LONG_TRACK_MS)
    right_pct = _handle_center_x(slider) / WIDTH * 100
    # Before the fix these were ~1.3% and ~98.3%.
    assert left_pct <= 1.0, f"playhead starts {left_pct:.1f}% in, should sit at the left edge"
    assert right_pct >= 99.0, f"playhead ends at {right_pct:.1f}%, should reach the right edge"


def test_playhead_position_is_proportional_across_the_track():
    """No systematic drift at any point — the old geometry skewed increasingly rightward."""
    slider = _slider()
    for fraction in (0.0, 0.25, 0.5, 0.75, 0.9, 1.0):
        slider.setValue(int(LONG_TRACK_MS * fraction))
        actual = _handle_center_x(slider) / WIDTH
        assert abs(actual - fraction) <= 0.015, (
            f"at {fraction:.0%} the playhead sits at {actual:.1%} (>1.5% off)"
        )


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
