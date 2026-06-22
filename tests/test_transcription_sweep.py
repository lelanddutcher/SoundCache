from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from sound_vault.settings import index_path_for_vault
from sound_vault.ui.view_model import LibraryViewModel


def _vm(tmp_path: Path) -> LibraryViewModel:
    vault = tmp_path / "vault"
    (vault / "sounds").mkdir(parents=True)
    return LibraryViewModel(
        vault_root=vault, index_path=index_path_for_vault(vault),
        load_sidecars=False, sidecar_mode="summary",
    )


def _record(music_id, *, transcript_text="", transcript_path=None, folder=None, audio=None):
    # Duck-typed stand-in for SoundRecord (transcript_state + transcription_targets
    # only read these attributes).
    return SimpleNamespace(
        music_id=music_id, transcript_text=transcript_text, transcript_path=transcript_path,
        folder_path=folder, local_audio_path=audio,
    )


def test_transcription_targets_only_pending(tmp_path):
    vm = _vm(tmp_path)
    f = tmp_path / "f"
    f.mkdir()
    audio = f / "a.m4a"
    audio.write_bytes(b"x")
    vm._records_by_id = {
        "pending": _record("pending", folder=f, audio=audio),                      # has audio, no transcript
        "done": _record("done", transcript_text="hi", folder=f, audio=audio),      # already transcribed
        "no_audio": _record("no_audio", folder=f, audio=None),                     # nothing to transcribe
    }
    targets = vm.transcription_targets()
    ids = [mid for mid, _folder, _audio in targets]
    assert ids == ["pending"]


def test_transcription_targets_scoped_to_music_ids(tmp_path):
    vm = _vm(tmp_path)
    f = tmp_path / "f"
    f.mkdir()
    audio = f / "a.m4a"
    audio.write_bytes(b"x")
    vm._records_by_id = {
        "a": _record("a", folder=f, audio=audio),
        "b": _record("b", folder=f, audio=audio),
    }
    targets = vm.transcription_targets(["a"])
    assert [mid for mid, *_ in targets] == ["a"]


def test_transcribe_targets_writes_transcript_with_injected_transcriber(tmp_path):
    vm = _vm(tmp_path)
    folder = tmp_path / "sound"
    folder.mkdir()
    audio = folder / "clip.m4a"
    audio.write_bytes(b"\x00\x00")
    (folder / "metadata.json").write_text(json.dumps({"tiktok_music_id": "X", "paths": {}}), encoding="utf-8")

    def fake_transcriber(_audio_path):
        return {"text": "hello world", "language": "en", "model": "base", "engine": "faster-whisper"}

    count = vm.transcribe_targets([("X", folder, audio)], transcriber=fake_transcriber)
    assert count == 1
    meta = json.loads((folder / "metadata.json").read_text())
    assert meta["speech_transcript_v2"]["text"] == "hello world"
    assert meta["transcription"]["local"]["status"] == "ok"


def test_transcribe_targets_empty_is_noop(tmp_path):
    vm = _vm(tmp_path)
    assert vm.transcribe_targets([]) == 0


def test_transcribe_targets_no_transcriber_returns_zero(tmp_path):
    vm = _vm(tmp_path)
    folder = tmp_path / "s"
    folder.mkdir()
    audio = folder / "a.m4a"
    audio.write_bytes(b"x")
    # transcriber=None and we don't want it to build a real one → monkeypatch via env
    import os
    os.environ["SOUND_VAULT_DISABLE_TRANSCRIBE"] = "1"
    try:
        assert vm.transcribe_targets([("X", folder, audio)]) == 0
    finally:
        os.environ.pop("SOUND_VAULT_DISABLE_TRANSCRIBE", None)
