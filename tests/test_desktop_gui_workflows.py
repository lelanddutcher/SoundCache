from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QAbstractItemView, QApplication, QMessageBox

import sound_vault.ui.desktop as desktop_module
from sound_vault.ui.desktop import SoundVaultWindow


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_desktop_gui_qa_harness_exercises_core_editor_workflows(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUND_VAULT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("SOUND_VAULT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOUND_VAULT_DISABLE_AUTO_INDEX", "1")
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    sound_dir = vault / "sounds" / "hot - Hot Hook"
    videos_dir = sound_dir / "videos"
    catalog.mkdir(parents=True)
    videos_dir.mkdir(parents=True)
    audio = sound_dir / "hot.m4a"
    artwork = sound_dir / "artwork.jpg"
    transcript = sound_dir / "transcript.json"
    clip = videos_dir / "01-clip-creator.mp4"
    shot = videos_dir / "01-clip-creator.jpg"
    for path, payload in ((audio, b"audio"), (artwork, b"jpg"), (clip, b"mp4"), (shot, b"jpg")):
        path.write_bytes(payload)
    full_transcript = "needle drop catchphrase " + " ".join(f"line-{idx:03d}" for idx in range(80))
    transcript.write_text(json.dumps({"text": full_transcript, "language": "en"}), encoding="utf-8")
    (sound_dir / "metadata.json").write_text(
        json.dumps(
            {
                "tiktok_music_id": "hot",
                "tiktok_visible_title": "Hot Hook",
                "source_artist": "Creator",
                "paths": {"folder": str(sound_dir), "audio": str(audio), "artwork": str(artwork)},
                "usage_count": 500000,
                "associated_video_count": 1,
                "canonical_url": "https://www.tiktok.com/music/Hot-Hook-hot",
            }
        ),
        encoding="utf-8",
    )
    (sound_dir / "associated_videos_manifest.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "rank": 1,
                        "video_id": "clip",
                        "author_handle": "creator",
                        "video_url": "https://www.tiktok.com/@creator/video/clip",
                        "downloaded_video_path": str(clip),
                        "screenshot_path": str(shot),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (catalog / "sounds.jsonl").write_text(
        json.dumps(
            {
                "tiktok_music_id": "hot",
                "tiktok_visible_title": "Hot Hook",
                "canonical_url": "https://www.tiktok.com/music/Hot-Hook-hot",
                "paths": {"folder": str(sound_dir)},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    app = _app()
    window = SoundVaultWindow(vault_root=vault)
    window.show()
    app.processEvents()
    window.vm.rebuild_index()
    window.refresh_table()
    window.refresh_portal_tabs()

    assert window.table.rowCount() == 1
    assert window.table.itemDelegateForColumn(0).__class__.__name__ == "FavoriteButtonDelegate"
    assert window.table.itemDelegateForColumn(1).__class__.__name__ == "PlayButtonDelegate"
    assert bool(window.table.item(0, 1).data(desktop_module.PLAYABLE_ROLE))
    window._set_media_filter("has_transcript")
    assert window.table.rowCount() == 1
    window.search_box.setFocus()
    app.processEvents()
    window.search_box.setText("needle drop")
    window.search_box.setCursorPosition(len(window.search_box.text()))
    window.refresh_table()
    assert window.table.rowCount() == 1
    assert window.search_box.hasFocus()
    assert window.search_box.cursorPosition() == len("needle drop")
    window._set_usage_filter("over_100k")
    assert window.table.rowCount() == 1
    window.table.sortItems(7, Qt.SortOrder.DescendingOrder)
    assert window.table.item(0, 7).text() == "500000"
    window.table.selectRow(0)
    window.update_preview_from_selection()
    assert window.current_preview_record is not None
    assert window.transcript_text.toPlainText() == full_transcript
    assert "transcript: available" in window.preview_meta.text()
    assert full_transcript[:180] not in window.preview_meta.text()
    assert window.current_preview_record.associated_videos[0].video_path == clip
    assert window.open_video.isEnabled()
    assert window.open_tiktok_sound.isEnabled()
    opened_urls = []
    monkeypatch.setattr(desktop_module.QDesktopServices, "openUrl", lambda url: opened_urls.append(url.toString()))
    window.open_selected_tiktok_sound()
    assert opened_urls == ["https://www.tiktok.com/music/Hot-Hook-hot"]
    assert window.artwork_label.size().width() == 210
    # Portal count-tabs were replaced by the ArchiveHealthPanel (coverage %).
    # 1 sound, 1 with a transcript -> 100% transcript coverage.
    window.refresh_portal_tabs()
    assert window.archive_health_panel._values["missing_transcript"].text() == "100%"
    window.close()


def test_desktop_user_notes_editor_saves_and_searches(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUND_VAULT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("SOUND_VAULT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOUND_VAULT_DISABLE_AUTO_INDEX", "1")
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    sound_dir = vault / "sounds" / "n1 - Notable"
    catalog.mkdir(parents=True)
    sound_dir.mkdir(parents=True)
    (sound_dir / "metadata.json").write_text(
        json.dumps(
            {
                "tiktok_music_id": "n1",
                "tiktok_visible_title": "Notable",
                "source_artist": "Creator",
                "paths": {"folder": str(sound_dir)},
            }
        ),
        encoding="utf-8",
    )
    (catalog / "sounds.jsonl").write_text(
        json.dumps({"tiktok_music_id": "n1", "tiktok_visible_title": "Notable", "paths": {"folder": str(sound_dir)}})
        + "\n",
        encoding="utf-8",
    )

    app = _app()
    window = SoundVaultWindow(vault_root=vault)
    window.show()
    app.processEvents()
    window.vm.rebuild_index()
    window.refresh_table()

    # The User notes editor lives under the Transcript section of the inspector.
    assert hasattr(window, "user_notes_edit")
    window.table.selectRow(0)
    window.update_preview_from_selection()
    app.processEvents()
    assert window._notes_music_id == "n1"
    assert window.user_notes_edit.toPlainText() == ""  # nothing saved yet

    # Type a note and flush (the real flow debounces; we flush directly here).
    window.user_notes_edit.setPlainText("perfect for skate edits")
    window._flush_user_notes()

    # Persisted to metadata.json (file-native truth)...
    meta = json.loads((sound_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["user_notes"] == "perfect for skate edits"
    # ...and searchable through the normal search box.
    window.search_box.setText("skate")
    window.refresh_table()
    assert window.table.rowCount() == 1

    # Re-selecting the row reloads the saved note into the editor.
    window.clear_preview()
    assert window.user_notes_edit.toPlainText() == ""
    window.table.selectRow(0)
    window.update_preview_from_selection()
    app.processEvents()
    assert window.user_notes_edit.toPlainText() == "perfect for skate edits"
    window.close()


def test_desktop_empty_transcript_box_explains_pending_state(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUND_VAULT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("SOUND_VAULT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOUND_VAULT_DISABLE_AUTO_INDEX", "1")
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    sound_dir = vault / "sounds" / "p1 - Pending"
    catalog.mkdir(parents=True)
    sound_dir.mkdir(parents=True)
    audio = sound_dir / "p1.m4a"
    audio.write_bytes(b"audio")
    # Has audio, no transcript yet -> "pending" state.
    (sound_dir / "metadata.json").write_text(
        json.dumps(
            {
                "tiktok_music_id": "p1",
                "tiktok_visible_title": "Pending",
                "paths": {"folder": str(sound_dir), "audio": str(audio)},
            }
        ),
        encoding="utf-8",
    )
    (catalog / "sounds.jsonl").write_text(
        json.dumps({"tiktok_music_id": "p1", "tiktok_visible_title": "Pending", "paths": {"folder": str(sound_dir)}})
        + "\n",
        encoding="utf-8",
    )

    app = _app()
    window = SoundVaultWindow(vault_root=vault)
    window.show()
    app.processEvents()
    window.vm.rebuild_index()
    window.refresh_table()
    window.table.selectRow(0)
    window.update_preview_from_selection()
    app.processEvents()

    # Empty box, but the placeholder + meta line explain why.
    assert window.transcript_text.toPlainText() == ""
    assert "Not transcribed yet" in window.transcript_text.placeholderText()
    assert "transcript: not run yet" in window.preview_meta.text()
    window.close()


def test_library_popularity_sort_is_numeric_not_text(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUND_VAULT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("SOUND_VAULT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOUND_VAULT_DISABLE_AUTO_INDEX", "1")
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    catalog.mkdir(parents=True)
    rows = [
        ("small", "Small", 9997),
        ("large", "Large", 997600),
        ("tiny", "Tiny", 9954),
    ]
    for music_id, title, usage_count in rows:
        sound_dir = vault / "sounds" / music_id
        sound_dir.mkdir(parents=True)
        (sound_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "tiktok_music_id": music_id,
                    "tiktok_visible_title": title,
                    "source_artist": "Creator",
                    "usage_count": usage_count,
                    "paths": {"folder": str(sound_dir)},
                }
            ),
            encoding="utf-8",
        )
    (catalog / "sounds.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "tiktok_music_id": music_id,
                    "tiktok_visible_title": title,
                    "source_artist": "Creator",
                    "usage_count": usage_count,
                    "paths": {"folder": str(vault / "sounds" / music_id)},
                }
            )
            for music_id, title, usage_count in rows
        )
        + "\n",
        encoding="utf-8",
    )

    app = _app()
    window = SoundVaultWindow(vault_root=vault)
    window.show()
    app.processEvents()
    window.vm.rebuild_index()
    window.refresh_table()

    assert [window.table.item(row, 7).text() for row in range(3)] == ["997600", "9997", "9954"]
    window.handle_library_header_clicked(7)
    app.processEvents()  # header click defers refresh via QTimer.singleShot
    assert [window.table.item(row, 7).text() for row in range(3)] == ["9954", "9997", "997600"]
    window.close()


def _write_duplicate_sound(vault, catalog, music_id: str, title: str, artist: str, duration: int) -> None:
    sound_dir = vault / "sounds" / f"{music_id} - {title}"
    sound_dir.mkdir(parents=True)
    audio = sound_dir / f"{music_id}.m4a"
    audio.write_bytes(b"audio")
    (sound_dir / "transcript.json").write_text(
        json.dumps({"text": f"{title} lyric phrase", "language": "en"}),
        encoding="utf-8",
    )
    (sound_dir / "metadata.json").write_text(
        json.dumps(
            {
                "tiktok_music_id": music_id,
                "tiktok_visible_title": title,
                "source_artist": artist,
                "duration_seconds": duration,
                "paths": {"folder": str(sound_dir), "audio": str(audio)},
            }
        ),
        encoding="utf-8",
    )
    with (catalog / "sounds.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "tiktok_music_id": music_id,
                    "tiktok_visible_title": title,
                    "source_artist": artist,
                    "paths": {"folder": str(sound_dir)},
                }
            )
            + "\n"
        )


def _write_transport_sound(
    vault,
    catalog,
    music_id: str,
    title: str,
    usage_count: int,
    *,
    playable: bool = True,
) -> None:
    sound_dir = vault / "sounds" / f"{music_id} - {title}"
    sound_dir.mkdir(parents=True)
    paths = {"folder": str(sound_dir)}
    if playable:
        audio = sound_dir / f"{music_id}.m4a"
        audio.write_bytes(b"audio")
        paths["audio"] = str(audio)
    (sound_dir / "metadata.json").write_text(
        json.dumps(
            {
                "tiktok_music_id": music_id,
                "tiktok_visible_title": title,
                "source_artist": "Transport Test",
                "usage_count": usage_count,
                "duration_seconds": 6,
                "paths": paths,
            }
        ),
        encoding="utf-8",
    )
    with (catalog / "sounds.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "tiktok_music_id": music_id,
                    "tiktok_visible_title": title,
                    "source_artist": "Transport Test",
                    "usage_count": usage_count,
                    "paths": paths,
                }
            )
            + "\n"
        )


def test_continuous_play_advances_through_visible_playable_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUND_VAULT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("SOUND_VAULT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOUND_VAULT_DISABLE_AUTO_INDEX", "1")
    monkeypatch.setenv("SOUND_VAULT_DISABLE_AFPLAY", "1")  # exercise QMediaPlayer mock, not macOS afplay
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    catalog.mkdir(parents=True)
    _write_transport_sound(vault, catalog, "alpha", "Alpha Hook", 300, playable=True)
    _write_transport_sound(vault, catalog, "silent", "Silent Gap", 200, playable=False)
    _write_transport_sound(vault, catalog, "beta", "Beta Hook", 100, playable=True)

    app = _app()
    window = SoundVaultWindow(vault_root=vault)
    window.show()
    app.processEvents()
    window.vm.rebuild_index()
    window.refresh_table()

    from PySide6.QtMultimedia import QMediaPlayer

    class FakeAudioPlayer:
        def __init__(self):
            self.source_value = desktop_module.QUrl()
            self.state = QMediaPlayer.PlaybackState.StoppedState
            self.play_count = 0

        def source(self):
            return self.source_value

        def setSource(self, source):
            self.source_value = source

        def playbackState(self):
            return self.state

        def play(self):
            self.play_count += 1
            self.state = QMediaPlayer.PlaybackState.PlayingState

        def pause(self):
            self.state = QMediaPlayer.PlaybackState.PausedState

        def stop(self):
            self.state = QMediaPlayer.PlaybackState.StoppedState

    fake_player = FakeAudioPlayer()
    window.audio_player = fake_player
    monkeypatch.setattr(window, "_ensure_audio_player", lambda: True)
    assert [window.table.item(row, 2).text() for row in range(window.table.rowCount())] == [
        "Alpha Hook",
        "Silent Gap",
        "Beta Hook",
    ]

    window.table.selectRow(0)
    window.update_preview_from_selection()
    window.continuous_play_button.setChecked(True)
    window.toggle_continuous_play()
    window.play_selected_sound()
    assert fake_player.play_count == 1
    assert fake_player.source_value.toLocalFile().endswith("alpha.m4a")

    window._player_media_status_changed(QMediaPlayer.MediaStatus.EndOfMedia)
    app.processEvents()
    assert window.table.currentRow() == 2
    assert window.current_preview_record.title == "Beta Hook"
    assert fake_player.play_count == 2
    assert fake_player.source_value.toLocalFile().endswith("beta.m4a")
    window.close()


def test_random_transport_selects_and_plays_a_random_playable_row(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUND_VAULT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("SOUND_VAULT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOUND_VAULT_DISABLE_AUTO_INDEX", "1")
    monkeypatch.setenv("SOUND_VAULT_DISABLE_AFPLAY", "1")  # exercise QMediaPlayer mock, not macOS afplay
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    catalog.mkdir(parents=True)
    _write_transport_sound(vault, catalog, "one", "One Shot", 30, playable=True)
    _write_transport_sound(vault, catalog, "two", "Two Shot", 20, playable=True)
    _write_transport_sound(vault, catalog, "dead", "Dead Air", 10, playable=False)

    app = _app()
    window = SoundVaultWindow(vault_root=vault)
    window.show()
    app.processEvents()
    window.vm.rebuild_index()
    window.refresh_table()

    from PySide6.QtMultimedia import QMediaPlayer

    class FakeAudioPlayer:
        def __init__(self):
            self.source_value = desktop_module.QUrl()
            self.state = QMediaPlayer.PlaybackState.StoppedState
            self.play_count = 0

        def source(self):
            return self.source_value

        def setSource(self, source):
            self.source_value = source

        def playbackState(self):
            return self.state

        def play(self):
            self.play_count += 1
            self.state = QMediaPlayer.PlaybackState.PlayingState

        def pause(self):
            self.state = QMediaPlayer.PlaybackState.PausedState

        def stop(self):
            self.state = QMediaPlayer.PlaybackState.StoppedState

    fake_player = FakeAudioPlayer()
    window.audio_player = fake_player
    monkeypatch.setattr(window, "_ensure_audio_player", lambda: True)
    window.table.selectRow(0)
    window.update_preview_from_selection()
    monkeypatch.setattr(desktop_module.random, "choice", lambda rows: rows[-1])
    expected_row = window._playable_library_rows()[-1]
    expected_title = window.table.item(expected_row, 2).text()

    window.play_random_sound()

    assert window.table.currentRow() == expected_row
    assert window.current_preview_record.title == expected_title
    assert fake_player.play_count == 1
    assert fake_player.source_value.toLocalFile().endswith("two.m4a")
    window.close()


def test_library_multi_select_can_create_manual_duplicate_review_group(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUND_VAULT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("SOUND_VAULT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOUND_VAULT_DISABLE_AUTO_INDEX", "1")
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    reports = vault / "reports"
    catalog.mkdir(parents=True)
    reports.mkdir(parents=True)
    _write_duplicate_sound(vault, catalog, "1", "Manual One", "Creator", 7)
    _write_duplicate_sound(vault, catalog, "2", "Manual Two", "Creator", 8)
    _write_duplicate_sound(vault, catalog, "3", "Different", "Other", 9)

    app = _app()
    window = SoundVaultWindow(vault_root=vault)
    window.show()
    app.processEvents()
    window.vm.rebuild_index()
    window.refresh_table()

    assert window.table.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection
    window.table.clearSelection()
    model = window.table.selectionModel()
    selected_ids = []
    for row in range(window.table.rowCount()):
        music_id = window.table.item(row, desktop_module.PLAY_COL).data(Qt.ItemDataRole.UserRole)
        if music_id in {"1", "2"}:
            selected_ids.append(str(music_id))
            model.select(
                window.table.model().index(row, 0),
                QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
            )
    app.processEvents()
    assert set(selected_ids) == {"1", "2"}
    assert set(window._selected_library_music_ids()) == {"1", "2"}

    window.mark_selected_library_as_duplicate()
    app.processEvents()

    assert window.stack.currentWidget() == window.dedupe_view
    assert window.dedupe_groups_table.rowCount() == 1
    assert window.dedupe_candidates_table.rowCount() == 2
    group_id = window.dedupe_groups_table.item(0, 0).data(Qt.ItemDataRole.UserRole)
    assert str(group_id).startswith("manual-")
    assert {window.dedupe_candidates_table.item(row, 1).text() for row in range(2)} == {"1", "2"}
    assert (vault / "reports" / "duplicate-candidates.json").exists()
    window.close()


def test_duplicate_review_page_updates_inspector_and_closes_marked_groups(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUND_VAULT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("SOUND_VAULT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOUND_VAULT_DISABLE_AUTO_INDEX", "1")
    monkeypatch.setenv("SOUND_VAULT_DISABLE_AFPLAY", "1")  # exercise QMediaPlayer mock, not macOS afplay
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    reports = vault / "reports"
    catalog.mkdir(parents=True)
    reports.mkdir(parents=True)
    _write_duplicate_sound(vault, catalog, "1", "Kickoff", "Creator", 7)
    _write_duplicate_sound(vault, catalog, "2", "Kickoff Copy", "Creator", 8)
    _write_duplicate_sound(vault, catalog, "3", "Snare Drop", "Editor", 9)
    _write_duplicate_sound(vault, catalog, "4", "Snare Drop Alt", "Editor", 10)
    (reports / "duplicate-candidates.json").write_text(
        json.dumps(
            [
                {
                    "group_key": "kickoff",
                    "score": 0.97,
                    "candidates": [{"music_id": "1"}, {"music_id": "2"}],
                },
                {
                    "group_key": "snare-drop",
                    "score": 0.88,
                    "candidates": [{"music_id": "3"}, {"music_id": "4"}],
                },
            ]
        ),
        encoding="utf-8",
    )

    app = _app()
    window = SoundVaultWindow(vault_root=vault)
    window.show()
    app.processEvents()
    window.vm.rebuild_index()
    window.show_view("dedupe")
    app.processEvents()

    assert window.dedupe_groups_table.rowCount() == 2
    assert window.dedupe_candidates_table.rowCount() == 2
    for row in range(window.dedupe_groups_table.rowCount()):
        if window.dedupe_groups_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == "kickoff":
            window.dedupe_groups_table.selectRow(row)
            break
    app.processEvents()
    window.dedupe_candidates_table.selectRow(0)
    app.processEvents()
    assert window.preview_title.text() == "Kickoff"
    assert "duration: 0:07" in window.preview_meta.text()
    assert "Kickoff lyric phrase" in window.transcript_text.toPlainText()
    assert "transcript: available" in window.preview_meta.text()
    assert window.transport_play_button.isEnabled()

    class FakePlayer:
        def __init__(self):
            self.source_value = None
            self.played = False

        def setSource(self, source):
            self.source_value = source

        def play(self):
            self.played = True

        def stop(self):
            self.played = False

    fake_player = FakePlayer()
    window.audio_player = fake_player
    monkeypatch.setattr(window, "_ensure_audio_player", lambda: True)
    window.play_dedupe_candidate(0)
    assert fake_player.played
    assert "Playing duplicate candidate 1" in window.playback_status.text()

    window.record_selected_duplicate_decision("duplicates")
    app.processEvents()
    assert window.dedupe_groups_table.rowCount() == 1
    assert window.dedupe_groups_table.item(0, 0).data(Qt.ItemDataRole.UserRole) == "snare-drop"

    window.record_selected_duplicate_decision("not_duplicates")
    app.processEvents()
    assert window.dedupe_groups_table.rowCount() == 0
    assert window.dedupe_candidates_table.rowCount() == 0
    assert window.preview_title.text() == "Select a duplicate candidate"
    window.close()


def test_duplicate_review_quarantine_moves_folders_and_removes_group(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUND_VAULT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("SOUND_VAULT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOUND_VAULT_DISABLE_AUTO_INDEX", "1")
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    reports = vault / "reports"
    catalog.mkdir(parents=True)
    reports.mkdir(parents=True)
    _write_duplicate_sound(vault, catalog, "1", "Keep Me", "Creator", 7)
    _write_duplicate_sound(vault, catalog, "2", "Move Me", "Creator", 8)
    duplicate_folder = vault / "sounds" / "2 - Move Me"
    (reports / "duplicate-candidates.json").write_text(
        json.dumps(
            [
                {
                    "group_key": "quarantine-test",
                    "score": 0.99,
                    "candidates": [{"music_id": "1"}, {"music_id": "2"}],
                }
            ]
        ),
        encoding="utf-8",
    )

    app = _app()
    window = SoundVaultWindow(vault_root=vault)
    window.show()
    app.processEvents()
    window.vm.rebuild_index()
    window.show_view("dedupe")
    window.dedupe_candidates_table.selectRow(0)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)

    window.quarantine_selected_duplicates()
    app.processEvents()

    assert window.dedupe_groups_table.rowCount() == 0
    assert not duplicate_folder.exists()
    assert any((vault / "reports" / "duplicate-quarantine").glob("*/2 - Move Me"))
    window.close()
