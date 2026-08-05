"""Seeking the playback slider on macOS (external ffplay/afplay backend).

afplay can't seek, so the external backend prefers ffplay and implements seek by
restarting playback at the requested offset (`ffplay -ss`). These tests cover the
command builder and the seek dispatch without spawning real audio processes.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

import sound_vault.ui.desktop as desktop_module
from sound_vault.ui.desktop import SoundVaultWindow


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


# --- command builder (pure) ------------------------------------------------


def test_external_command_prefers_ffplay_with_offset(monkeypatch):
    monkeypatch.setattr(desktop_module.shutil, "which", lambda name: f"/bin/{name}" if name == "ffplay" else None)
    cmd, seekable = SoundVaultWindow._external_player_command(Path("/tmp/a.m4a"), 5000)
    assert seekable is True
    assert cmd[0] == "/bin/ffplay"
    assert "-nodisp" in cmd and "-autoexit" in cmd
    assert "-ss" in cmd and cmd[cmd.index("-ss") + 1] == "5.000"
    assert cmd[-1] == "/tmp/a.m4a"


def test_external_command_ffplay_no_offset_omits_ss(monkeypatch):
    monkeypatch.setattr(desktop_module.shutil, "which", lambda name: f"/bin/{name}" if name == "ffplay" else None)
    cmd, seekable = SoundVaultWindow._external_player_command(Path("/tmp/a.m4a"), 0)
    assert seekable is True
    assert "-ss" not in cmd


def test_external_command_falls_back_to_afplay_without_seek(monkeypatch):
    monkeypatch.setattr(desktop_module.shutil, "which", lambda name: "/usr/bin/afplay" if name == "afplay" else None)
    cmd, seekable = SoundVaultWindow._external_player_command(Path("/tmp/a.m4a"), 5000)
    assert seekable is False
    assert cmd == ["/usr/bin/afplay", "/tmp/a.m4a"]


def test_external_command_none_when_no_player(monkeypatch):
    monkeypatch.setattr(desktop_module.shutil, "which", lambda name: None)
    assert SoundVaultWindow._external_player_command(Path("/tmp/a.m4a"), 0) == (None, False)


# --- seek dispatch ---------------------------------------------------------


def _window(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUND_VAULT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("SOUND_VAULT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOUND_VAULT_DISABLE_AUTO_INDEX", "1")
    vault = tmp_path / "vault"
    (vault / "catalog").mkdir(parents=True)
    (vault / "catalog" / "sounds.jsonl").write_text("", encoding="utf-8")
    _app()
    return SoundVaultWindow(vault_root=vault)


def test_seek_restarts_external_player_at_offset(tmp_path, monkeypatch):
    window = _window(tmp_path, monkeypatch)
    try:
        calls = []
        monkeypatch.setattr(window, "_play_external_audio", lambda target, start_ms=0: calls.append((target, start_ms)) or True)
        window.external_audio_target = tmp_path / "a.m4a"
        window._external_seek_supported = True
        window._external_audio_started_at = None  # skip the dead-zone guard

        window.seek_playback(12_000)
        assert calls == [(tmp_path / "a.m4a", 12_000)]
    finally:
        window.close()


def test_seek_ignored_for_afplay_only_backend(tmp_path, monkeypatch):
    window = _window(tmp_path, monkeypatch)
    try:
        calls = []
        monkeypatch.setattr(window, "_play_external_audio", lambda target, start_ms=0: calls.append((target, start_ms)) or True)
        window.external_audio_target = tmp_path / "a.m4a"
        window._external_seek_supported = False  # afplay can't seek

        window.seek_playback(12_000)
        assert calls == []  # no restart attempted
        assert "ffmpeg" in window.playback_status.text().lower()
    finally:
        window.close()


def test_seek_dead_zone_skips_tiny_moves(tmp_path, monkeypatch):
    import time

    window = _window(tmp_path, monkeypatch)
    try:
        calls = []
        monkeypatch.setattr(window, "_play_external_audio", lambda target, start_ms=0: calls.append((target, start_ms)) or True)
        window.external_audio_target = tmp_path / "a.m4a"
        window._external_seek_supported = True
        window._external_audio_base_offset_ms = 10_000
        window._external_audio_started_at = time.monotonic()

        window.seek_playback(10_100)  # ~100ms from current -> negligible
        assert calls == []
    finally:
        window.close()


def test_external_progress_anchors_to_seek_offset(tmp_path, monkeypatch):
    import time

    window = _window(tmp_path, monkeypatch)
    try:
        window._external_audio_duration_ms = 60_000
        window.progress_slider.setRange(0, 60_000)
        window._external_audio_base_offset_ms = 30_000
        window._external_audio_started_at = time.monotonic()
        window._update_external_progress()
        # position should be at least the seek offset (30s), not near zero
        assert window.progress_slider.value() >= 30_000
    finally:
        window.close()


# --- duration probe fallback (scrubber works without metadata duration) -----


def test_probe_audio_duration_uses_ffprobe_and_caches(tmp_path, monkeypatch):
    import subprocess as _subprocess
    from types import SimpleNamespace

    window = _window(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(desktop_module.shutil, "which", lambda name: "/bin/ffprobe" if name == "ffprobe" else None)
        runs = []

        def fake_run(cmd, **kwargs):
            runs.append(cmd)
            return SimpleNamespace(stdout="12.345\n", returncode=0)

        monkeypatch.setattr(desktop_module.subprocess, "run", fake_run)
        target = tmp_path / "clip.m4a"
        assert window._probe_audio_duration_ms(target) == 12_345
        # Cached: a second call must not re-run ffprobe.
        assert window._probe_audio_duration_ms(target) == 12_345
        assert len(runs) == 1
    finally:
        window.close()


def test_probe_audio_duration_zero_when_ffprobe_missing(tmp_path, monkeypatch):
    window = _window(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(desktop_module.shutil, "which", lambda name: None)
        assert window._probe_audio_duration_ms(tmp_path / "clip.m4a") == 0
    finally:
        window.close()


# --- preview re-render must not stomp a live playhead -----------------------


def _record(audio: Path, folder: Path, *, duration=None):
    from sound_vault.vault.indexer import SoundRecord

    return SoundRecord(
        music_id="123", title="t", artist="a", tags=(), status="ingested", raw={},
        folder_path=folder, local_audio_path=audio, duration_seconds=duration,
    )


def _quiet_preview(window, monkeypatch, audio):
    monkeypatch.setattr(window.vm, "play_target_for", lambda _r: audio)
    for name in ("_populate_artwork", "_populate_evidence", "_populate_videos", "_load_user_notes"):
        monkeypatch.setattr(window, name, lambda *_a, **_k: None)


def test_preview_rerender_during_playback_keeps_the_slider_range(tmp_path, monkeypatch):
    """`update_preview_from_selection` renders the preview twice: once immediately and
    again when the async hydration lands — which is often AFTER playback started. That
    second render used the record's stored duration only, so for a sound with no stored
    duration it reset the range to (0, 0): a slider that can never move. The playhead
    froze at the far left for the whole track while the time label kept counting.
    """
    window = _window(tmp_path, monkeypatch)
    try:
        audio = tmp_path / "clip.m4a"
        audio.write_bytes(b"\x00")
        record = _record(audio, tmp_path, duration=None)
        # Playback already resolved a real duration (via the ffprobe fallback).
        window._external_audio_duration_ms = 45_000
        window.progress_slider.setRange(0, 45_000)
        window.progress_slider.setValue(12_000)
        window.playing_music_id = "123"  # this record is the one currently playing
        _quiet_preview(window, monkeypatch, audio)

        window._show_preview_record(record)

        assert window.progress_slider.maximum() == 45_000, "hydration reset the range mid-playback"
        assert window.progress_slider.value() == 12_000, "hydration rewound the playhead mid-playback"
    finally:
        window.close()


def test_preview_uses_a_cached_probe_when_the_record_has_no_duration(tmp_path, monkeypatch):
    """Not playing: the preview should still show a real total for a sound with no stored
    duration, reusing a previously probed value (cache read only — no subprocess here)."""
    window = _window(tmp_path, monkeypatch)
    try:
        audio = tmp_path / "clip.m4a"
        audio.write_bytes(b"\x00")
        record = _record(audio, tmp_path, duration=None)
        window._duration_probe_cache[str(audio)] = 45_000  # probed on a previous play
        _quiet_preview(window, monkeypatch, audio)

        window._show_preview_record(record)

        assert window.progress_slider.maximum() == 45_000
        assert "0:45" in window.time_label.text()
    finally:
        window.close()
