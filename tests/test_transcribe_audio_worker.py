from __future__ import annotations

import importlib.util
import json
from pathlib import Path


_SPEC = importlib.util.spec_from_file_location(
    "transcribe_audio",
    Path(__file__).resolve().parents[1] / "scripts" / "transcribe_audio.py",
)
assert _SPEC and _SPEC.loader
transcribe_audio = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(transcribe_audio)


def test_iter_pending_folders_skips_completed_and_error_sidecars_do_not_count_as_done(tmp_path):
    vault = tmp_path / "vault"
    done = vault / "sounds" / "111 - Done - Artist"
    errored = vault / "sounds" / "222 - Error - Artist"
    missing_audio = vault / "sounds" / "333 - Missing - Artist"
    for folder in (done, errored, missing_audio):
        folder.mkdir(parents=True)
    (done / "audio.m4a").write_bytes(b"audio")
    (done / "transcript.json").write_text("{}", encoding="utf-8")
    (errored / "audio.m4a").write_bytes(b"audio")
    (errored / "transcription_error.json").write_text("{}", encoding="utf-8")

    pending = transcribe_audio.iter_pending_folders(vault, force=False)

    assert pending == [errored]


def test_update_metadata_records_transcript_path_and_searchable_summary(tmp_path):
    folder = tmp_path / "sound"
    folder.mkdir()
    metadata_path = folder / "metadata.json"
    metadata_path.write_text(json.dumps({"paths": {}}), encoding="utf-8")
    transcript_path = folder / "transcript.json"
    payload = {
        "text": "cost of deletion versus wishing you had it",
        "language": "en",
        "engine": "faster-whisper",
        "model": "tiny",
        "duration_seconds": 12.3,
    }

    transcribe_audio._update_metadata(folder, transcript_path, payload)

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["paths"]["transcript"] == str(transcript_path)
    assert metadata["speech_transcript"]["text"] == payload["text"]
    assert metadata["speech_transcript"]["has_text"] is True
    assert metadata["duration_seconds"] == 12.3
