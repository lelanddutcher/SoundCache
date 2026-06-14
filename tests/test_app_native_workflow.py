from __future__ import annotations

import json
from pathlib import Path

from sound_vault.dependency_diagnostics import diagnose_dependencies
from sound_vault.settings import AppSettings
from sound_vault.workflows.import_wizard import ImportWizard, ImportWizardStage
from sound_vault.workers.result import WorkerRunResult, verify_durable_outputs, write_worker_run


def _favorite_export(path: Path) -> None:
    path.write_text(
        'Favorite Sounds": {"FavoriteSoundList": ['
        '{"Date":"2026-05-01 10:00:00","Link":"https://m.tiktok.com/h5/share/music/1234567890.html"},'
        '{"Date":"2026-05-02 10:00:00","Link":"https://m.tiktok.com/h5/share/music/2222222222.html"}'
        ']},',
        encoding="utf-8",
    )


def test_import_wizard_previews_repairs_normalizes_packages_and_verifies(tmp_path):
    vault = tmp_path / "vault"
    export = tmp_path / "favorite sounds list.json"
    _favorite_export(export)

    wizard = ImportWizard(vault_root=vault, date_label="qa", oembed_fetch_json=lambda _url: {"title": "♬ Test Sound", "author_name": "Creator", "provider_name": "TikTok"}, oembed_delay_seconds=0)

    preview = wizard.select_export(export)
    assert wizard.stage is ImportWizardStage.PREVIEWED
    assert preview.record_count == 2
    assert preview.unique_music_ids == 2
    assert preview.new_to_vault == 2

    normalized = wizard.normalize()
    assert normalized.json_path.exists()
    assert wizard.stage is ImportWizardStage.NORMALIZED

    enriched = wizard.enrich()
    assert enriched.summary.ok_count == 2
    assert wizard.stage is ImportWizardStage.ENRICHED

    packaged = wizard.package()
    assert packaged.summary.created_count == 2
    assert (vault / "catalog" / "sounds.jsonl").exists()
    assert wizard.stage is ImportWizardStage.PACKAGED

    report = wizard.rebuild_index_and_verify(search_terms=["Test Sound"])
    assert wizard.stage is ImportWizardStage.VERIFIED
    assert report.status == "ok"
    assert report.counts["metadata_records"] == 2
    assert report.counts["sound_folders"] == 2
    assert report.counts["search_hits"] >= 1
    assert Path(report.outputs["summary_json"]).exists()


def test_worker_run_result_writes_summary_jsonl_and_requires_real_artifacts(tmp_path):
    vault = tmp_path / "vault"
    artifact = vault / "sounds" / "123 - A" / "metadata.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text('{"tiktok_music_id":"123"}\n', encoding="utf-8")

    result = WorkerRunResult(
        worker="verification",
        status="ok",
        counts={"total": 1, "ok": 1, "errors": 0},
        outputs={"metadata": str(artifact)},
        verified_outputs=[str(artifact)],
        next_actions=[],
    )
    written = write_worker_run(vault, result)
    assert written.summary_path.exists()
    assert written.events_path.exists()
    payload = json.loads(written.summary_path.read_text(encoding="utf-8"))
    assert payload["status"] == "ok"
    assert payload["verified_outputs"] == [str(artifact)]

    broken = verify_durable_outputs([artifact, vault / "missing.wav"])
    assert broken.status == "partial"
    assert str(vault / "missing.wav") in broken.missing


def test_settings_store_transcription_capture_without_plaintext_api_key(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings = AppSettings(settings_path)
    settings.set_transcription_config(
        preferred_provider="cloud",
        cloud_provider="openai",
        cloud_base_url="https://api.openai.com/v1",
        cloud_model="gpt-4o-transcribe",
        local_engine="faster-whisper",
        local_model="base",
        model_cache_dir=str(tmp_path / "models"),
        demucs_enabled=True,
        demucs_model="htdemucs_ft",
    )
    settings.set_capture_config(aggressiveness="preview_audio", require_manual_login=True, max_batch_items=10, delay_seconds=15, stop_on_checkpoint=True)
    settings.set_secret_reference("cloud.openai.api_key", "keyring:sound-vault/cloud/openai/api_key")

    raw = settings_path.read_text(encoding="utf-8")
    assert "sk-" not in raw
    assert settings.transcription_config()["preferred_provider"] == "cloud"
    assert settings.capture_config()["aggressiveness"] == "preview_audio"
    assert settings.secret_reference("cloud.openai.api_key") == "keyring:sound-vault/cloud/openai/api_key"


def test_dependency_diagnostics_separates_path_packages_models_and_acceleration(tmp_path):
    report = diagnose_dependencies(
        model_cache_dir=tmp_path / "models",
        local_model="base",
        which=lambda name: f"/usr/bin/{name}" if name in {"ffmpeg", "ffprobe"} else None,
        package_available=lambda name: name == "faster_whisper",
        version_runner=lambda cmd: "ffmpeg version n6.0" if cmd[0] == "ffmpeg" else "ffprobe version n6.0",
        torch_info=lambda: {"cuda_available": False, "mps_available": True, "platform": "Darwin", "machine": "arm64"},
    )
    assert report.tools["ffmpeg"].installed is True
    assert report.python_packages["faster_whisper"].installed is True
    assert report.python_packages["openai_whisper"].installed is False
    assert report.models["base"].installed is False
    assert report.demucs.acceleration == "mps"
    assert "PATH executable" in report.notes[0]
