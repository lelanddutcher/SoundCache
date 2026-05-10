import json

from sound_vault.vault.indexer import build_index


def test_build_index_merges_mutable_folder_metadata_for_usage_and_video_counts(tmp_path):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    sound_dir = vault / "sounds" / "123 - Mutable"
    catalog.mkdir(parents=True)
    sound_dir.mkdir(parents=True)
    (catalog / "sounds.jsonl").write_text(
        json.dumps(
            {
                "tiktok_music_id": "123",
                "tiktok_visible_title": "Mutable Sound",
                "usage_count": None,
                "associated_video_count": 0,
                "paths": {"folder": str(sound_dir)},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (sound_dir / "metadata.json").write_text(
        json.dumps(
            {
                "tiktok_music_id": "123",
                "usage_count": 148_700,
                "usage_count_label": "148.7K videos",
                "associated_video_count": 3,
            }
        ),
        encoding="utf-8",
    )

    [record] = build_index(vault)

    assert record.usage_count == 148_700
    assert record.associated_video_count == 3
