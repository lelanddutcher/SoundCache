"""The inspector reports *why* a transcript box is empty.

Four distinct states, derived from already-persisted fields, so the user can tell
"no speech detected" apart from "not run yet" apart from "no audio".
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from sound_vault.ui.desktop import SoundVaultWindow
from sound_vault.vault.indexer import SoundRecord


def _record(**kw) -> SoundRecord:
    base = dict(music_id="m", title="T", artist="A", tags=(), status="approved", raw={})
    base.update(kw)
    return SoundRecord(**base)


def test_state_available_when_transcript_text_present():
    rec = _record(transcript_text="we ride till the lights go out", transcript_language="en")
    assert SoundVaultWindow._transcript_state(rec) == "available"
    assert SoundVaultWindow._format_transcript_status(rec).startswith("transcript: available")


def test_state_empty_when_transcribed_but_no_speech(tmp_path):
    # A sidecar exists on disk (transcription ran) but produced no text -> instrumental.
    sidecar = tmp_path / "transcript.json"
    sidecar.write_text("{}", encoding="utf-8")
    rec = _record(transcript_text="", transcript_path=sidecar)
    assert SoundVaultWindow._transcript_state(rec) == "empty"
    assert "no speech" in SoundVaultWindow._format_transcript_status(rec)
    assert "instrumental" in SoundVaultWindow._transcript_placeholder(rec)


def test_deleted_sidecar_is_not_reported_as_empty(tmp_path):
    # transcript_path points at a file that no longer exists -> don't claim the
    # sound is an instrumental; treat it as pending re-transcription instead.
    missing = tmp_path / "gone.json"  # intentionally never created
    rec = _record(transcript_text="", transcript_path=missing, local_audio_path=tmp_path / "a.m4a")
    assert SoundVaultWindow._transcript_state(rec) == "pending"


def test_state_no_audio_when_nothing_to_transcribe():
    rec = _record(transcript_text="", transcript_path=None, local_audio_path=None)
    assert SoundVaultWindow._transcript_state(rec) == "no_audio"
    assert "no audio" in SoundVaultWindow._format_transcript_status(rec).lower()
    assert "nothing to transcribe" in SoundVaultWindow._transcript_placeholder(rec)


def test_state_pending_when_audio_present_but_not_run(tmp_path):
    rec = _record(transcript_text="", transcript_path=None, local_audio_path=tmp_path / "sound.m4a")
    assert SoundVaultWindow._transcript_state(rec) == "pending"
    assert "not run yet" in SoundVaultWindow._format_transcript_status(rec)
    assert "Not transcribed yet" in SoundVaultWindow._transcript_placeholder(rec)


def test_whitespace_only_transcript_is_not_available(tmp_path):
    sidecar = tmp_path / "transcript.json"
    sidecar.write_text("{}", encoding="utf-8")
    rec = _record(transcript_text="   \n  ", transcript_path=sidecar)
    assert SoundVaultWindow._transcript_state(rec) == "empty"
