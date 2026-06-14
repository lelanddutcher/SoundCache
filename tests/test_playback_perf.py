from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ---- DB: cheap popularity count (replaces building 2000 records to len()) ----

def test_count_usage_at_least(tmp_path):
    from sound_vault.db.index_db import IndexDatabase
    from sound_vault.vault.indexer import SoundRecord

    db = IndexDatabase(tmp_path / "i.sqlite3")
    def rec(mid, usage):
        return SoundRecord(music_id=mid, title=mid.upper(), artist="", tags=(), status="approved", raw={}, usage_count=usage)
    db.rebuild([rec("a", 2_400_000), rec("b", 99_000), rec("c", None), rec("d", 150_000)])
    assert db.count_usage_at_least(100_000) == 2
    assert db.count_usage_at_least(1) == 3


# ---- GUI fixes (offscreen) ----

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

import sound_vault.ui.desktop as desktop_module
from sound_vault.ui.desktop import SoundVaultWindow


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _seed(vault, music_id="s1", title="Hook", playable=True):
    catalog = vault / "catalog"; catalog.mkdir(parents=True, exist_ok=True)
    sound_dir = vault / "sounds" / music_id
    sound_dir.mkdir(parents=True, exist_ok=True)
    paths = {"folder": str(sound_dir)}
    if playable:
        audio = sound_dir / f"{music_id}.m4a"; audio.write_bytes(b"\x00a")
        paths["audio"] = str(audio)
    md = {"tiktok_music_id": music_id, "tiktok_visible_title": title, "source_artist": "Creator",
          "usage_count": 5, "duration": 30, "paths": paths}
    (sound_dir / "metadata.json").write_text(json.dumps(md))
    (catalog / "sounds.jsonl").write_text(json.dumps(md) + "\n")


def _window(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUND_VAULT_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("SOUND_VAULT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOUND_VAULT_DISABLE_AUTO_INDEX", "1")
    monkeypatch.setenv("SOUND_VAULT_DISABLE_RELAY_POLL", "1")
    vault = tmp_path / "vault"
    _seed(vault)
    app = _app()
    w = SoundVaultWindow(vault_root=vault)
    w.show(); app.processEvents()
    w.vm.rebuild_index(); w.refresh_table(); app.processEvents()
    return w


def test_set_playing_marks_delegate(tmp_path, monkeypatch):
    w = _window(tmp_path, monkeypatch)
    try:
        w._set_playing("s1")
        assert w.playing_music_id == "s1"
        assert w.play_delegate.playing_music_id == "s1"
        w._set_playing(None)
        assert w.play_delegate.playing_music_id is None
    finally:
        w.close()


def test_external_progress_estimates_position(tmp_path, monkeypatch):
    w = _window(tmp_path, monkeypatch)
    try:
        w._external_audio_started_at = 1000.0
        w._external_audio_duration_ms = 30_000
        w.progress_slider.setRange(0, 30_000)
        monkeypatch.setattr(desktop_module.time, "monotonic", lambda: 1007.5)  # 7.5s elapsed
        w._update_external_progress()
        assert abs(w.progress_slider.value() - 7500) < 50
        assert "0:07" in w.time_label.text()
    finally:
        w.close()


def test_continuous_play_toggle_tracks_button(tmp_path, monkeypatch):
    w = _window(tmp_path, monkeypatch)
    try:
        w.continuous_play_button.setChecked(True)
        w.toggle_continuous_play()
        assert w.continuous_play_enabled is True
        w.continuous_play_button.setChecked(False)
        w.toggle_continuous_play()
        assert w.continuous_play_enabled is False
    finally:
        w.close()


def test_continuous_play_advances_to_next_playable_row(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUND_VAULT_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("SOUND_VAULT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOUND_VAULT_DISABLE_AUTO_INDEX", "1")
    monkeypatch.setenv("SOUND_VAULT_DISABLE_RELAY_POLL", "1")
    vault = tmp_path / "vault"
    _seed(vault, "a", "Alpha")
    # add a second playable sound to the same catalog
    catalog = vault / "catalog"
    d = vault / "sounds" / "b"; d.mkdir(parents=True)
    audio = d / "b.m4a"; audio.write_bytes(b"\x00a")
    md = {"tiktok_music_id": "b", "tiktok_visible_title": "Beta", "source_artist": "C",
          "usage_count": 5, "duration": 30, "paths": {"folder": str(d), "audio": str(audio)}}
    (d / "metadata.json").write_text(json.dumps(md))
    (catalog / "sounds.jsonl").write_text(
        (catalog / "sounds.jsonl").read_text() + json.dumps(md) + "\n"
    )
    app = _app()
    w = SoundVaultWindow(vault_root=vault)
    w.show(); app.processEvents()
    w.vm.rebuild_index(); w.refresh_table(); app.processEvents()
    try:
        assert w.table.rowCount() == 2
        w.continuous_play_enabled = True
        w.table.selectRow(0)
        calls = []
        monkeypatch.setattr(w, "_select_library_row", lambda row, play=False: calls.append((row, play)))
        w._play_next_continuous_sound()
        assert calls == [(1, True)]  # advanced to the next playable row and played it
    finally:
        w.close()


def test_toggle_favorite_updates_row_in_place(tmp_path, monkeypatch):
    w = _window(tmp_path, monkeypatch)
    try:
        assert w.table.rowCount() == 1
        fav_item = w.table.item(0, desktop_module.FAVORITE_COL)
        before = bool(fav_item.data(desktop_module.FAVORITE_ROLE))
        w.toggle_favorite_by_id("s1")
        # same row object updated in place (no full rebuild), star flipped
        assert w.table.rowCount() == 1
        assert bool(w.table.item(0, desktop_module.FAVORITE_COL).data(desktop_module.FAVORITE_ROLE)) != before
    finally:
        w.close()
