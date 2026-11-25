"""The fixture vault indexes cleanly and covers every row state the UI draws."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))

from fixture_vault import build_fixture_vault  # noqa: E402

from sound_vault.vault.indexer import build_index, transcript_state  # noqa: E402


def test_fixture_vault_indexes_every_state(tmp_path):
    made = build_fixture_vault(tmp_path, count=24)
    records = build_index(tmp_path)
    assert len(records) == 24 == len(made)
    states = {transcript_state(r) for r in records}
    assert states == {"available", "empty", "pending", "no_audio"}
    assert any(r.artwork_path is None for r in records)
    assert any(r.usage_count is None for r in records)
    assert any(r.associated_video_count == 0 for r in records)
    assert all(r.duration_seconds for r in records)


def test_fixture_vault_is_deterministic(tmp_path):
    a = build_fixture_vault(tmp_path / "a", count=6)
    b = build_fixture_vault(tmp_path / "b", count=6)
    assert [(s.music_id, s.title, s.bpm) for s in a] == [(s.music_id, s.title, s.bpm) for s in b]
    wav_a = (a[0].folder / "audio.wav").read_bytes()
    assert wav_a == (b[0].folder / "audio.wav").read_bytes()
