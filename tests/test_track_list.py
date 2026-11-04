"""The 0.5 track list: formatting, responsive layout, and the live window wiring."""
from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))

from fixture_vault import FIXTURE_NOW, build_fixture_vault  # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from sound_vault.ui.track_list import (  # noqa: E402
    RECORD_ROLE,
    format_bpm,
    format_duration,
    format_uses,
    initials,
    is_new,
    resample_peaks,
    row_layout,
    tag_lines,
    tile_color,
)

WIDTHS = [360, 480, 620, 760, 900, 1100, 1400, 1900]


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- formatting
def test_formatters_match_the_design():
    assert format_duration(131) == "2:11"
    assert format_duration(9.4) == "0:09"
    assert format_duration(None) == "—"
    assert format_bpm(139.6) == "140 BPM"
    assert format_bpm(None) == "—"
    assert format_uses(2_100_000) == "2.1M"
    assert format_uses(1_000_000) == "1M"
    assert format_uses(842_000) == "842K"
    assert format_uses(999) == "999"
    assert format_uses(None) == "—"
    assert tag_lines(("phonk", "drift", "car edits", "hype", "extra")) == ("phonk, drift", "car edits, hype")
    assert tag_lines(()) == ("", "")
    assert initials("sahara phonk") == "SP"
    assert initials("") == "♪"


def test_new_badge_is_seven_days():
    now = datetime(2026, 11, 3, tzinfo=UTC)
    assert is_new("2026-11-01T10:00:00+00:00", now)
    assert is_new("2026-10-27T00:00:00Z", now)
    assert not is_new("2026-10-20T00:00:00+00:00", now)
    assert not is_new("not a date", now)
    assert not is_new("", now)


def test_tile_color_is_stable_per_sound():
    assert tile_color("7362").name() == tile_color("7362").name()


def test_resample_peaks():
    assert resample_peaks([0.1, 0.9, 0.2, 0.4], 2) == [0.9, 0.4]
    assert len(resample_peaks([0.5] * 400, 97)) == 97
    assert resample_peaks([], 3) == [0.0, 0.0, 0.0]


# ---------------------------------------------------------------- layout
@pytest.mark.parametrize("density", ["comfortable", "compact"])
@pytest.mark.parametrize("width", WIDTHS)
def test_row_cells_never_overlap_or_leave_the_row(width, density):
    _app()
    row = QRect(0, 0, width, 76 if density == "comfortable" else 56)
    lay = row_layout(row, density)
    rects = lay.visible_rects()
    for r in rects:
        assert row.contains(r.adjusted(0, 0, -1, -1)) or r.width() == 0, (r, row)
    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            assert not a.intersects(b), (width, a, b)
    assert lay.text.width() >= 60
    # Every action keeps a 40×44 hit area.
    assert all(r.width() >= 40 and r.height() >= 44 for r in lay.actions.values())


def test_columns_drop_out_in_the_documented_order():
    _app()
    full = row_layout(QRect(0, 0, 1400, 76))
    assert full.tags and full.uses and full.wave and full.icons and len(full.actions) == 5
    no_tags = row_layout(QRect(0, 0, 960, 76))
    assert no_tags.tags is None and no_tags.uses is not None
    no_uses = row_layout(QRect(0, 0, 820, 76))
    assert no_uses.uses is None and no_uses.wave is not None
    slim_actions = row_layout(QRect(0, 0, 700, 76))
    assert set(slim_actions.actions) == {"favorite", "more"}
    no_wave = row_layout(QRect(0, 0, 560, 76))
    assert no_wave.wave is None and no_wave.length is not None
    phone = row_layout(QRect(0, 0, 360, 76))
    assert phone.icons is None and phone.length is None
    assert phone.text.width() >= 100


# ---------------------------------------------------------------- window
@pytest.fixture
def quiet_env(monkeypatch):
    for flag in (
        "SOUND_VAULT_DISABLE_AUTO_INDEX",
        "SOUND_VAULT_DISABLE_RELAY_POLL",
        "SOUND_VAULT_DISABLE_ONBOARDING",
        "SOUND_VAULT_DISABLE_TIKTOK_PROMPT",
        "SOUND_VAULT_DISABLE_ANALYSIS",
        "SOUND_VAULT_DISABLE_AUDIO",
    ):
        monkeypatch.setenv(flag, "1")


@pytest.fixture
def window(tmp_path, quiet_env):
    from sound_vault.ui.desktop import SoundVaultWindow

    app = _app()
    build_fixture_vault(tmp_path / "vault", count=24)
    w = SoundVaultWindow(vault_root=tmp_path / "vault")
    w.track_delegate.now = lambda: FIXTURE_NOW
    w.resize(1440, 900)
    w.show()
    app.processEvents()
    w.vm.rebuild_index()
    w.refresh_table()
    app.processEvents()
    yield w
    w.close()


def _click(view, index, x_offset_from_right=None, at=None):
    rect = view.visualRect(index)
    pos = at if at is not None else QPoint(rect.right() - x_offset_from_right, rect.center().y())
    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        event = QMouseEvent(kind, QPointF(pos), QPointF(view.viewport().mapToGlobal(pos)),
                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(view.viewport(), event)
    QApplication.processEvents()


def test_list_is_the_default_view_and_shares_the_tables_selection(window):
    assert window.library_stack.currentWidget() is window.track_list
    assert window.track_list.model() is window.table.model()
    assert window.track_list.selectionModel() is window.table.selectionModel()
    model = window.table.model()
    window.track_list.setCurrentIndex(model.index(3, 2))
    window.track_list.selectionModel().select(
        model.index(3, 2),
        window.track_list.selectionModel().SelectionFlag.ClearAndSelect | window.track_list.selectionModel().SelectionFlag.Rows,
    )
    QApplication.processEvents()
    assert window._selected_music_id() == model.music_id_at(3)


def test_view_switch_persists(window):
    window.view_mode_button.setChecked(True)
    assert window.library_stack.currentWidget() is window.table
    assert window.settings.library_view_mode() == "table"
    window.view_mode_button.setChecked(False)
    assert window.library_stack.currentWidget() is window.track_list
    assert window.settings.library_view_mode() == "list"


def test_row_buttons_route_to_the_right_actions(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window, "play_record_by_id", lambda mid: calls.append(("play", mid)))
    monkeypatch.setattr(window, "toggle_favorite_by_id", lambda mid: calls.append(("fav", mid)))
    monkeypatch.setattr(window, "track_copy_link", lambda mid: calls.append(("link", mid)))
    view = window.track_list
    model = view.model()
    index = model.index(0, 2)
    record = index.data(RECORD_ROLE)
    lay = window.track_delegate.layout_for(view.visualRect(index))
    _click(view, index, at=lay.play.center())
    _click(view, index, at=lay.actions["favorite"].center())
    _click(view, index, at=lay.actions["link"].center())
    assert calls == [("play", record.music_id), ("fav", record.music_id), ("link", record.music_id)]


def test_keyboard_favorite_and_play(window, monkeypatch):
    calls = []
    # Space is the window-wide play shortcut; it plays the selected sound.
    monkeypatch.setattr(window, "play_selected_sound", lambda: calls.append("play"))
    monkeypatch.setattr(window, "toggle_play_pause", lambda: calls.append("play"))
    monkeypatch.setattr(window, "toggle_favorite_by_id", lambda mid: calls.append(("fav", mid)))
    from PySide6.QtGui import QKeyEvent

    window.track_list.setFocus()
    for key in (Qt.Key.Key_F, Qt.Key.Key_Space):
        QApplication.sendEvent(window.track_list, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))
    assert calls[0][0] == "fav" and calls[1] == "play"


def test_bpm_and_vocals_filters_and_bpm_sort(window):
    from sound_vault.vault.analysis import analyze_sound_folder

    # Analyse a few sounds so some rows have a tempo.
    analysed = 0
    for record in list(window.current_rows):
        if record.local_audio_path is not None and record.folder_path is not None and analysed < 6:
            analyze_sound_folder(record.folder_path, record.local_audio_path)
            analysed += 1
    window.vm.rebuild_index()
    window.refresh_table()
    total = len(window.current_rows)

    window.bpm_filter.setCurrentIndex(window.bpm_filter.findData("unknown"))
    window.refresh_table()
    assert all(r.bpm is None for r in window.current_rows)
    assert len(window.current_rows) < total

    window.bpm_filter.setCurrentIndex(window.bpm_filter.findData("all"))
    window.vocals_only.setChecked(True)
    window.refresh_table()
    assert window.current_rows and all((r.transcript_text or "").strip() for r in window.current_rows)
    window.vocals_only.setChecked(False)

    window.sort_combo.setCurrentIndex(3)  # BPM
    window.refresh_table()
    bpms = [r.bpm for r in window.current_rows]
    known = [b for b in bpms if b is not None]
    assert known == sorted(known)
    assert bpms[: len(known)] == known  # unknown tempos last


def test_track_list_renders_at_every_width_without_errors(window, tmp_path):
    app = _app()
    for width in WIDTHS:
        window.track_list.resize(width, 600)
        app.processEvents()
        image = window.track_list.grab().toImage()
        assert not image.isNull()
        # Something was painted: not one flat color.
        colors = {image.pixel(x, y) for x in range(0, image.width(), 23) for y in range(0, image.height(), 17)}
        assert len(colors) > 4, width


def test_density_switch_changes_row_height(window):
    model = window.track_list.model()
    window.set_library_density("compact")
    QApplication.processEvents()
    assert window.track_list.visualRect(model.index(0, 2)).height() == 56
    assert window.settings.library_density() == "compact"
    window.set_library_density("comfortable")
    QApplication.processEvents()
    assert window.track_list.visualRect(model.index(0, 2)).height() == 76


def test_view_shortcuts_and_detail_toggle(window):
    # Offscreen windows never get keyboard focus, so trigger each shortcut directly
    # (found by its key sequence, which is what the user presses).
    from PySide6.QtGui import QKeySequence, QShortcut

    shortcuts = {sc.key().toString(): sc for sc in window.findChildren(QShortcut)}
    for keys, view in (("Ctrl+2", window.inbox_view), ("Ctrl+5", window.worker_view), ("Ctrl+1", window.library_view)):
        shortcuts[QKeySequence(keys).toString()].activated.emit()
        assert window.stack.currentWidget() is view, keys
    assert QKeySequence("Ctrl+I").toString() in shortcuts
    visible = window.preview_panel.isVisible()
    window.toggle_detail_pane()
    assert window.preview_panel.isVisible() is (not visible)


def test_accessible_text_names_each_row(window):
    model = window.table.model()
    text = model.index(0, 2).data(Qt.ItemDataRole.AccessibleTextRole)
    record = model.index(0, 2).data(RECORD_ROLE)
    assert record.title in text and "uses" in text or record.usage_count is None
