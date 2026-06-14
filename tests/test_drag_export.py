from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableWidgetItem

from sound_vault.ui.desktop import SoundTableWidget


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _row(table: SoundTableWidget, music_id: str) -> QTableWidgetItem:
    table.setRowCount(1)
    table.setColumnCount(1)
    item = QTableWidgetItem(music_id)
    item.setData(Qt.ItemDataRole.UserRole, music_id)
    table.setItem(0, 0, item)
    return item


def test_drag_exports_audio_file_url(tmp_path):
    _app()
    audio = tmp_path / "sound.m4a"
    audio.write_bytes(b"\x00audio")
    table = SoundTableWidget(0, 1)
    table.audio_path_resolver = lambda mid: audio if mid == "55" else None
    item = _row(table, "55")

    mime = table.mimeData([item])
    # External apps (Finder/Premiere) get the real file as a URL.
    assert mime.hasUrls()
    assert [u.toLocalFile() for u in mime.urls()] == [str(audio)]
    # Internal drag-to-bin format is still present.
    assert mime.hasFormat("application/x-sound-vault-music-id")
    # Text fallback is the path, not the bare music id.
    assert mime.text() == str(audio)


def test_drag_without_audio_falls_back_to_music_id(tmp_path):
    _app()
    table = SoundTableWidget(0, 1)
    table.audio_path_resolver = lambda mid: None  # no local audio
    item = _row(table, "99")
    mime = table.mimeData([item])
    assert not mime.hasUrls()
    assert mime.hasFormat("application/x-sound-vault-music-id")
    assert mime.text() == "99"


def test_drag_skips_missing_files(tmp_path):
    _app()
    table = SoundTableWidget(0, 1)
    table.audio_path_resolver = lambda mid: tmp_path / "does-not-exist.m4a"
    item = _row(table, "7")
    mime = table.mimeData([item])
    assert not mime.hasUrls()  # non-existent path is not exported
