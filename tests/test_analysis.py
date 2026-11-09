"""Waveform peaks and BPM (sound_vault.vault.analysis)."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))

from fixture_vault import build_fixture_vault, click_track  # noqa: E402

from sound_vault.vault import analysis  # noqa: E402
from sound_vault.vault.analysis import (  # noqa: E402
    DECODE_RATE,
    AnalysisError,
    analyze_sound_folder,
    compute_peaks,
    estimate_bpm,
    load_peaks,
    needs_analysis,
)

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _track(bpm: float, seconds: float, seed: int = 0) -> np.ndarray:
    return click_track(bpm, seconds, seed=seed, sample_rate=DECODE_RATE).astype(np.float32) / 32768.0


@pytest.mark.parametrize("bpm", [60, 70, 82, 90, 100, 108, 115, 120, 128, 140, 150, 160, 174, 190])
@pytest.mark.parametrize("seconds", [9, 31])
def test_bpm_matches_the_click_track_within_two(bpm, seconds):
    estimate, confidence = estimate_bpm(_track(bpm, seconds, seed=bpm))
    assert estimate is not None and abs(estimate - bpm) <= 2, (estimate, confidence)
    assert confidence >= 0.5


def test_noise_has_no_tempo():
    rng = np.random.default_rng(1)
    estimate, confidence = estimate_bpm(rng.uniform(-0.5, 0.5, DECODE_RATE * 20).astype(np.float32))
    assert estimate is None
    assert confidence < analysis.MIN_BPM_CONFIDENCE


def test_random_hits_have_no_tempo():
    """Free-time onsets (speech-like) should read as unknown, not a guessed number."""
    rng = np.random.default_rng(3)
    x = np.zeros(DECODE_RATE * 20, dtype=np.float32)
    for start in rng.integers(0, x.size - 500, 60):
        x[start : start + 400] += np.hanning(400).astype(np.float32) * rng.uniform(0.3, 1.0)
    estimate, _ = estimate_bpm(x)
    assert estimate is None


def test_clips_under_four_seconds_have_no_tempo():
    assert estimate_bpm(_track(120, 3.0)) == (None, 0.0)


def test_silence_has_no_tempo_and_flat_peaks():
    silent = np.zeros(DECODE_RATE * 10, dtype=np.float32)
    assert estimate_bpm(silent)[0] is None
    assert compute_peaks(silent) == [0.0] * analysis.PEAK_COUNT


def test_peaks_are_normalised_and_sized():
    peaks = compute_peaks(_track(120, 12), count=200)
    assert len(peaks) == 200
    assert max(peaks) == 1.0
    assert min(peaks) >= 0.0


def test_peaks_of_empty_input():
    assert compute_peaks(np.zeros(0, dtype=np.float32), count=5) == [0.0] * 5


@needs_ffmpeg
def test_analyze_sound_folder_writes_waveform_and_metadata(tmp_path):
    sound = next(s for s in build_fixture_vault(tmp_path, count=4) if s.has_audio and s.bpm)
    audio = sound.folder / "audio.wav"

    result = analyze_sound_folder(sound.folder, audio)

    assert result is not None
    assert abs(result.bpm - sound.bpm) <= 2
    waveform = json.loads((sound.folder / "waveform.json").read_text())
    assert waveform["version"] == analysis.ANALYSIS_VERSION
    assert len(waveform["peaks"]) == len(waveform["levels"]) == analysis.PEAK_COUNT
    meta = json.loads((sound.folder / "metadata.json").read_text())
    assert meta["bpm"] == result.bpm
    assert meta["paths"]["waveform"] == str(sound.folder / "waveform.json")
    assert meta["paths"]["audio"] == str(audio)  # existing fields survive
    assert meta["tiktok_visible_title"] == sound.title
    assert not needs_analysis(meta)
    assert load_peaks(sound.folder / "waveform.json") == waveform["levels"]


@needs_ffmpeg
def test_analysis_is_skipped_once_done_unless_forced(tmp_path):
    sound = next(s for s in build_fixture_vault(tmp_path, count=4) if s.has_audio)
    audio = sound.folder / "audio.wav"
    assert analyze_sound_folder(sound.folder, audio) is not None
    assert analyze_sound_folder(sound.folder, audio) is None
    assert analyze_sound_folder(sound.folder, audio, force=True) is not None


@needs_ffmpeg
def test_undecodable_audio_raises_analysis_error(tmp_path):
    folder = tmp_path / "s"
    folder.mkdir()
    bad = folder / "audio.m4a"
    bad.write_bytes(b"not audio at all")
    with pytest.raises(AnalysisError):
        analyze_sound_folder(folder, bad)
    assert not (folder / "waveform.json").exists()


def test_load_peaks_tolerates_missing_and_corrupt_files(tmp_path):
    assert load_peaks(None) is None
    assert load_peaks(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert load_peaks(bad) is None
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"peaks": "x"}))
    assert load_peaks(wrong) is None


@needs_ffmpeg
def test_bpm_and_waveform_survive_index_rebuild_and_read(tmp_path):
    from sound_vault.db.index_db import IndexDatabase
    from sound_vault.vault.indexer import build_index

    vault = tmp_path / "vault"
    sounds = build_fixture_vault(vault, count=6)
    target = next(s for s in sounds if s.has_audio and s.bpm)
    analyze_sound_folder(target.folder, target.folder / "audio.wav")

    records = {r.music_id: r for r in build_index(vault)}
    analysed = records[target.music_id]
    assert analysed.bpm is not None and abs(analysed.bpm - target.bpm) <= 2
    assert analysed.waveform_path == target.folder / "waveform.json"
    untouched = next(r for mid, r in records.items() if mid != target.music_id)
    assert untouched.bpm is None and untouched.waveform_path is None

    db = IndexDatabase(tmp_path / "index.sqlite3")
    db.rebuild(list(records.values()))
    cached = db.get(target.music_id)
    assert cached.bpm == analysed.bpm
    assert cached.waveform_path == analysed.waveform_path
    db.upsert(analysed)  # the upsert path writes the same columns
    assert db.get(target.music_id).bpm == analysed.bpm


def test_index_created_before_bpm_gains_the_columns(tmp_path):
    import sqlite3

    from sound_vault.db.index_db import IndexDatabase

    db_path = tmp_path / "index.sqlite3"
    IndexDatabase(db_path)  # current schema
    with sqlite3.connect(db_path) as db:  # simulate a 0.4.x index: drop the new columns
        db.execute("ALTER TABLE sounds DROP COLUMN bpm")
        db.execute("ALTER TABLE sounds DROP COLUMN waveform_path")
        db.execute(
            "INSERT INTO sounds (music_id, title, artist, tags, status, search_text) "
            "VALUES ('m1', 'Old', 'A', '', 'unreviewed', 'old')"
        )
    index = IndexDatabase(db_path)
    with sqlite3.connect(db_path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(sounds)")}
    assert {"bpm", "waveform_path"} <= columns
    old = index.get("m1")
    assert old.bpm is None and old.waveform_path is None


def test_levels_show_a_drop_that_peaks_hide():
    rng = np.random.default_rng(0)
    loud = rng.uniform(-1, 1, DECODE_RATE * 4).astype(np.float32)
    # A quiet stretch that still has a few full-scale transients, like a mastered breakdown.
    quiet = (rng.uniform(-1, 1, DECODE_RATE * 2) * 0.15).astype(np.float32)
    quiet[:: DECODE_RATE // 8] = 1.0
    samples = np.concatenate([loud, quiet, loud])
    peaks = compute_peaks(samples, count=10)
    levels = analysis.compute_levels(samples, count=10)
    assert min(peaks) > 0.9  # peaks: a flat block
    assert max(levels) == 1.0 and levels[5] < 0.2  # levels: the drop is obvious
    assert analysis.compute_levels(np.zeros(0, dtype=np.float32), count=3) == [0.0] * 3


def test_load_peaks_prefers_levels_and_reads_old_files(tmp_path):
    new = tmp_path / "new.json"
    new.write_text(json.dumps({"version": 2, "peaks": [1.0, 1.0], "levels": [0.2, 1.0]}))
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"version": 1, "peaks": [0.9, 1.0]}))
    assert load_peaks(new) == [0.2, 1.0]
    assert load_peaks(old) == [0.9, 1.0]
