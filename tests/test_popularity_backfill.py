import json

from sound_vault.workers.popularity_backfill import (
    UsageBackfillResult,
    parse_usage_count_label,
    update_metadata_usage_count,
)


def test_parse_usage_count_label_handles_tiktok_video_counts():
    assert parse_usage_count_label("214 videos") == (214, "214 videos")
    assert parse_usage_count_label("475.7K videos") == (475_700, "475.7K videos")
    assert parse_usage_count_label("6.9M videos") == (6_900_000, "6.9M videos")
    assert parse_usage_count_label("1.2B videos") == (1_200_000_000, "1.2B videos")
    assert parse_usage_count_label("not a count") == (None, "")


def test_update_metadata_usage_count_preserves_source_details(tmp_path):
    folder = tmp_path / "123 - Sound"
    folder.mkdir()
    metadata_path = folder / "metadata.json"
    metadata_path.write_text(json.dumps({"tiktok_music_id": "123", "usage_count": None}), encoding="utf-8")

    result = UsageBackfillResult(
        music_id="123",
        usage_count=475_700,
        usage_count_label="475.7K videos",
        source="dom_text",
        captured_at="2026-05-10T20:00:00Z",
        ok=True,
    )

    update_metadata_usage_count(folder, result)

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["usage_count"] == 475_700
    assert metadata["usage_count_label"] == "475.7K videos"
    assert metadata["usage_count_source"] == "dom_text"
    assert metadata["usage_count_captured_at"] == "2026-05-10T20:00:00Z"
    assert metadata["evidence"]["usage_count_backfill"]["ok"] is True
