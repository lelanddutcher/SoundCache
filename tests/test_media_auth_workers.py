from __future__ import annotations

import json
from pathlib import Path

from sound_vault.auth.tiktok_session import CaptureAggressiveness, TikTokSessionProbe, validate_capture_request
from sound_vault.workers.artwork import backfill_artwork
from sound_vault.workers.transcription import CloudASRConfig, transcribe_cloud_batch


def _sound_folder(vault: Path, music_id: str = "123") -> Path:
    folder = vault / "sounds" / f"{music_id} - Test Sound - Creator"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "metadata.json").write_text(
        json.dumps({
            "tiktok_music_id": music_id,
            "music_id": music_id,
            "tiktok_visible_title": "Test Sound",
            "paths": {"folder": str(folder), "audio": None, "artwork": None, "transcript": None},
            "assets": [],
        }) + "\n",
        encoding="utf-8",
    )
    return folder


def test_capture_request_requires_explicit_session_for_audio_or_video(tmp_path):
    metadata_only = validate_capture_request(CaptureAggressiveness.METADATA_ONLY, session_probe=None)
    assert metadata_only.allowed is True
    assert metadata_only.session_required is False

    blocked = validate_capture_request(CaptureAggressiveness.PREVIEW_AUDIO, session_probe=None)
    assert blocked.allowed is False
    assert blocked.session_required is True
    assert "login" in blocked.message.lower()

    checkpoint = validate_capture_request(
        CaptureAggressiveness.ASSOCIATED_VIDEOS,
        session_probe=TikTokSessionProbe(status="checkpoint", tested_url="https://www.tiktok.com/music/-123", final_url="", title=""),
    )
    assert checkpoint.allowed is False
    assert checkpoint.stop_reason == "checkpoint"

    ok = validate_capture_request(
        CaptureAggressiveness.FULL_AUDIO,
        session_probe=TikTokSessionProbe(status="ok", tested_url="https://www.tiktok.com/music/-123", final_url="https://www.tiktok.com/music/-123", title="TikTok"),
    )
    assert ok.allowed is True


def test_artwork_worker_prefers_real_artwork_and_labels_fallback(tmp_path):
    vault = tmp_path / "vault"
    folder = _sound_folder(vault)

    def fetch_artwork(_metadata):
        return b"real-artwork", "image/jpeg", "sound_artwork"

    result = backfill_artwork(vault, fetch_artwork=fetch_artwork)
    metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    artwork_path = Path(metadata["paths"]["artwork"])
    assert result.status == "ok"
    assert artwork_path.exists()
    assert metadata["media_capture"]["artwork_status"] == "ok"
    assert metadata["assets"][-1]["asset_type"] == "sound_artwork"

    fallback_vault = tmp_path / "fallback"
    fallback_folder = _sound_folder(fallback_vault, "456")
    result = backfill_artwork(fallback_vault, fetch_artwork=lambda _metadata: (b"fallback", "image/png", "associated_video_cover_fallback"))
    metadata = json.loads((fallback_folder / "metadata.json").read_text(encoding="utf-8"))
    assert result.status == "ok"
    assert metadata["media_capture"]["artwork_status"] == "fallback"
    assert metadata["assets"][-1]["fallback_label"] == "associated_video_cover_fallback"


def test_cloud_asr_worker_writes_sidecars_updates_metadata_and_never_logs_api_key(tmp_path):
    vault = tmp_path / "vault"
    folder = _sound_folder(vault)
    audio = folder / "preview.m4a"
    audio.write_bytes(b"fake audio bytes")
    metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    metadata["paths"]["audio"] = str(audio)
    (folder / "metadata.json").write_text(json.dumps(metadata) + "\n", encoding="utf-8")

    def transcriber(path, config):
        assert path == audio
        assert config.api_key == "sk-secret-should-not-be-written"
        return {"text": "this is the catchphrase", "language": "en", "segments": [], "duration_seconds": 1.2}

    result = transcribe_cloud_batch(
        vault,
        config=CloudASRConfig(provider="openai", base_url="https://api.openai.com/v1", model="gpt-4o-transcribe", api_key="sk-secret-should-not-be-written"),
        transcriber=transcriber,
    )
    metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    # paths.transcript is stored vault-relative (portable); resolve against the vault.
    rel_transcript = metadata["paths"]["transcript"]
    assert not Path(rel_transcript).is_absolute()
    transcript_path = vault / rel_transcript
    assert result.status == "ok"
    assert transcript_path.exists()
    assert metadata["speech_transcript_v2"]["best_text"] == "this is the catchphrase"
    serialized = (folder / "metadata.json").read_text(encoding="utf-8") + transcript_path.read_text(encoding="utf-8")
    assert "sk-secret" not in serialized
