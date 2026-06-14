from __future__ import annotations

import json

from sound_vault.vault.indexer import build_index
from sound_vault.vault.package_writer import package_imported_sounds
from sound_vault.workers.oembed import enrich_favorite_sounds_oembed


def test_oembed_worker_resumes_checkpoint_and_continues_after_row_failures(tmp_path):
    imports = tmp_path / "catalog" / "imports"
    imports.mkdir(parents=True)
    normalized = imports / "favorite_sounds_import_normalized_2026-05-18.json"
    normalized.write_text(
        json.dumps(
            {
                "records": [
                    {"tiktok_music_id": "1", "canonical_url_guess": "https://www.tiktok.com/music/-1"},
                    {"tiktok_music_id": "2", "canonical_url_guess": "https://www.tiktok.com/music/-2"},
                    {"tiktok_music_id": "", "source_link": "https://example.invalid/blank"},
                ]
            }
        ),
        encoding="utf-8",
    )
    checkpoint = imports / "favorite_sounds_oembed_enriched_2026-05-18.checkpoint.json"
    checkpoint.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "tiktok_music_id": "1",
                        "canonical_url_guess": "https://www.tiktok.com/music/-1",
                        "oembed_status": "ok",
                        "oembed_title": "Already Done",
                        "oembed_author_name": "Creator One",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def fetch(url: str) -> dict[str, str]:
        assert "-2" in url
        return {"title": "Fresh Title", "author_name": "Creator Two", "provider_name": "TikTok"}

    result = enrich_favorite_sounds_oembed(
        normalized,
        imports,
        date_label="2026-05-18",
        delay_seconds=0,
        checkpoint_every=1,
        fetch_json=fetch,
    )

    assert result.summary.record_count == 3
    assert result.summary.ok_count == 2
    assert result.summary.error_count == 1
    assert result.summary.resumed_count == 1
    assert result.summary.skipped_blank_ids == 1
    assert not checkpoint.exists()
    assert result.json_path.exists()
    assert result.csv_path.exists()
    assert [row["oembed_title"] for row in result.records[:2]] == ["Already Done", "Fresh Title"]


def test_package_imported_sounds_creates_metadata_only_packages_and_preserves_existing_assets(tmp_path):
    vault = tmp_path / "vault"
    imports = vault / "catalog" / "imports"
    imports.mkdir(parents=True)
    existing_folder = vault / "sounds" / "2 - Existing"
    existing_folder.mkdir(parents=True)
    audio = existing_folder / "existing.m4a"
    artwork = existing_folder / "artwork.jpg"
    audio.write_bytes(b"audio")
    artwork.write_bytes(b"art")
    (existing_folder / "metadata.json").write_text(
        json.dumps(
            {
                "tiktok_music_id": "2",
                "tiktok_visible_title": "Existing",
                "status": "packaged_sample",
                "paths": {"folder": str(existing_folder), "audio": str(audio), "artwork": str(artwork)},
            }
        ),
        encoding="utf-8",
    )
    enriched = imports / "favorite_sounds_oembed_enriched_2026-05-18.json"
    enriched.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "tiktok_music_id": "1",
                        "saved_at": "2026-05-01 12:00:00",
                        "canonical_url_guess": "https://www.tiktok.com/music/-1",
                        "mobile_music_url": "https://m.tiktok.com/h5/share/music/1.html",
                        "oembed_status": "ok",
                        "oembed_title": "New Import",
                        "oembed_author_name": "Creator",
                        "oembed_provider_name": "TikTok",
                    },
                    {
                        "tiktok_music_id": "2",
                        "canonical_url_guess": "https://www.tiktok.com/music/-2",
                        "oembed_status": "ok",
                        "oembed_title": "Existing Updated",
                        "oembed_author_name": "Existing Creator",
                    },
                    {"tiktok_music_id": "", "oembed_status": "error"},
                ]
            }
        ),
        encoding="utf-8",
    )

    result = package_imported_sounds(enriched, vault)

    assert result.summary.created_count == 1
    assert result.summary.updated_count == 1
    assert result.summary.skipped_blank_ids == 1
    assert result.summary.failed_count == 1
    created_metadata = json.loads(
        (vault / "sounds" / "1 - New Import - Creator" / "metadata.json").read_text(encoding="utf-8")
    )
    assert created_metadata["status"] == "metadata_only"
    assert created_metadata["audit"]["missing_audio"] is True
    existing_metadata = json.loads((existing_folder / "metadata.json").read_text(encoding="utf-8"))
    assert existing_metadata["paths"]["audio"] == str(audio)
    assert existing_metadata["paths"]["artwork"] == str(artwork)
    assert result.catalog_jsonl.exists()
    assert result.catalog_csv.exists()
    records = build_index(vault)
    assert {record.music_id for record in records} == {"1", "2"}
    imported = next(record for record in records if record.music_id == "1")
    assert imported.title == "New Import"
    assert imported.status == "metadata_only"

    second = package_imported_sounds(enriched, vault)
    assert second.summary.created_count == 0
    assert second.summary.updated_count == 2
    catalog_ids = [
        json.loads(line)["tiktok_music_id"]
        for line in (vault / "catalog" / "sounds.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert catalog_ids == ["1", "2"]
