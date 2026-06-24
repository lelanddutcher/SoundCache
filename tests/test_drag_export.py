from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from sound_vault.ui.desktop import LibraryTableModel


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _model(music_id: str, resolver) -> tuple[LibraryTableModel, list]:
    model = LibraryTableModel()
    model.audio_path_resolver = resolver
    model.set_rows([SimpleNamespace(music_id=music_id)], favorites=set(), playable=set())
    indexes = [model.index(0, col) for col in range(model.columnCount())]
    return model, indexes


def test_drag_exports_audio_file_url(tmp_path):
    _app()
    audio = tmp_path / "sound.m4a"
    audio.write_bytes(b"\x00audio")
    model, indexes = _model("55", lambda mid: audio if mid == "55" else None)

    mime = model.mimeData(indexes)
    # External apps (Finder/Premiere) get the real file as a URL.
    assert mime.hasUrls()
    assert [u.toLocalFile() for u in mime.urls()] == [str(audio)]
    # Internal drag-to-bin format is still present.
    assert mime.hasFormat("application/x-sound-vault-music-id")
    # Text fallback is the path, not the bare music id.
    assert mime.text() == str(audio)


def test_drag_without_audio_falls_back_to_music_id(tmp_path):
    _app()
    model, indexes = _model("99", lambda mid: None)  # no local audio
    mime = model.mimeData(indexes)
    assert not mime.hasUrls()
    assert mime.hasFormat("application/x-sound-vault-music-id")
    assert mime.text() == "99"


def test_drag_skips_missing_files(tmp_path):
    _app()
    model, indexes = _model("7", lambda mid: tmp_path / "does-not-exist.m4a")
    mime = model.mimeData(indexes)
    assert not mime.hasUrls()  # non-existent path is not exported
