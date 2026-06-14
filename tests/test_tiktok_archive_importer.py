from __future__ import annotations

import csv
import json

import pytest

from sound_vault.importers.tiktok_archive import (
    extract_music_id,
    load_favorite_sound_rows,
    normalized_tiktok_url_variants,
    repair_tiktok_favorite_sounds_json,
    write_normalized_favorite_sounds_import,
)


def test_tiktok_archive_importer_repairs_fragment_and_preserves_source_file(tmp_path):
    source = tmp_path / "favorite sounds list.json"
    original_text = """
Favorite Sounds": {
  "FavoriteSoundList": [
    {
      "Date": "2026-05-02 15:58:30",
      "Link": "https://m.tiktok.com/h5/share/music/6817565543474661378.html"
    },
    {
      "Date": "2021-01-09 21:56:26",
      "Link": "https://m.tiktok.com/h5/share/music/655741.html"
    }
  ]
},
"""
    source.write_text(original_text, encoding="utf-8")

    result = write_normalized_favorite_sounds_import(
        source,
        tmp_path / "vault" / "catalog" / "imports",
        date_label="2026-05-18",
    )

    assert source.read_text(encoding="utf-8") == original_text
    assert result.summary.record_count == 2
    assert result.summary.unique_music_ids == 2
    assert result.summary.blank_ids == 0
    assert result.summary.duplicate_music_ids == 0
    assert result.summary.date_min == "2021-01-09 21:56:26"
    assert result.summary.date_max == "2026-05-02 15:58:30"
    assert result.summary.by_year == {"2021": 1, "2026": 1}
    assert result.records[0].tiktok_music_id == "6817565543474661378"
    assert result.records[0].canonical_url_guess == "https://www.tiktok.com/music/-6817565543474661378"
    assert result.records[0].mobile_music_url == "https://m.tiktok.com/h5/share/music/6817565543474661378.html"
    assert result.json_path.name == "favorite_sounds_import_normalized_2026-05-18.json"
    assert result.csv_path.exists()
    assert result.summary_path.exists()

    payload = json.loads(result.json_path.read_text(encoding="utf-8"))
    assert payload["records"][1]["tiktok_music_id"] == "655741"
    with result.csv_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["source"] == "tiktok_data_export_favorite_sounds"


def test_tiktok_archive_importer_counts_duplicates_blanks_and_malformed_rows(tmp_path):
    source = tmp_path / "favorite sounds list.json"
    source.write_text(
        json.dumps(
            {
                "Favorite Sounds": {
                    "FavoriteSoundList": [
                        {"Date": "2026-01-01 00:00:00", "Link": "https://www.tiktok.com/music/-12345"},
                        {"Date": "2026-01-02 00:00:00", "Link": "https://m.tiktok.com/h5/share/music/12345.html"},
                        {"Date": "2026-01-03 00:00:00", "Link": "https://example.com/not-a-music-link"},
                        "not a row",
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    result = write_normalized_favorite_sounds_import(source, tmp_path / "imports", date_label="test")

    assert result.summary.record_count == 3
    assert result.summary.unique_music_ids == 1
    assert result.summary.blank_ids == 1
    assert result.summary.duplicate_music_ids == 1
    assert result.summary.malformed_rows == 1
    assert [record.import_index for record in result.records] == [1, 2, 3]


def test_tiktok_archive_importer_supports_valid_json_and_root_favorite_sound_list(tmp_path):
    source = tmp_path / "favorite.json"
    source.write_text(
        json.dumps(
            {
                "FavoriteSoundList": [
                    {
                        "saved_at": "2020-01-09 21:56:26",
                        "source_link": "https://www.tiktok.com/music/Sound-Name-998877",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    records, malformed_rows = load_favorite_sound_rows(source)

    assert malformed_rows == 0
    assert records[0].saved_at == "2020-01-09 21:56:26"
    assert records[0].tiktok_music_id == "998877"


def test_tiktok_archive_music_id_extraction_handles_observed_url_shapes():
    assert extract_music_id("https://m.tiktok.com/h5/share/music/6817565543474661378.html") == "6817565543474661378"
    assert extract_music_id("https://www.tiktok.com/music/-6817565543474661378") == "6817565543474661378"
    assert extract_music_id("https://www.tiktok.com/music/Some-Title-655741") == "655741"
    assert extract_music_id("https://example.com/video/6817565543474661378") == ""


def test_tiktok_archive_importer_rejects_unknown_shapes():
    with pytest.raises(ValueError):
        repair_tiktok_favorite_sounds_json("not favorite sounds")


def test_tiktok_archive_importer_marks_existing_vault_matches_by_id_and_url(tmp_path):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    catalog.mkdir(parents=True)
    (catalog / "sounds.jsonl").write_text(
        json.dumps(
            {
                "tiktok_music_id": "12345",
                "tiktok_visible_title": "Catalog Existing",
                "canonical_url": "https://www.tiktok.com/music/-12345",
                "mobile_music_url": "https://m.tiktok.com/h5/share/music/12345.html",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    folder = vault / "sounds" / "99999 - Source URL Existing"
    folder.mkdir(parents=True)
    source_url = "https://www.tiktok.com/@creator/video/abc123?share_item_id=abc123"
    (folder / "metadata.json").write_text(
        json.dumps(
            {
                "tiktok_music_id": "99999",
                "source_link": source_url,
                "paths": {"folder": str(folder)},
            }
        ),
        encoding="utf-8",
    )
    source = tmp_path / "favorite sounds list.json"
    source.write_text(
        json.dumps(
            {
                "Favorite Sounds": {
                    "FavoriteSoundList": [
                        {"Date": "2026-01-01", "Link": "https://m.tiktok.com/h5/share/music/12345.html"},
                        {"Date": "2026-01-02", "Link": "https://www.tiktok.com/@creator/video/abc123?foo=bar"},
                        {"Date": "2026-01-03", "Link": "https://m.tiktok.com/h5/share/music/45678.html"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    result = write_normalized_favorite_sounds_import(
        source,
        vault / "catalog" / "imports",
        date_label="dedupe",
        vault_root=vault,
    )

    assert [record.vault_match_status for record in result.records] == [
        "already_in_vault_by_music_id",
        "already_in_vault_by_source_link",
        "new_to_vault",
    ]
    assert result.records[0].vault_match_music_id == "12345"
    assert result.records[1].vault_match_music_id == "99999"
    assert result.records[1].vault_match_folder == str(folder)
    assert result.summary.already_in_vault == 2
    assert result.summary.new_to_vault == 1
    assert result.summary.ambiguous_matches == 0
    assert result.summary.vault_match_counts == {
        "already_in_vault_by_music_id": 1,
        "already_in_vault_by_source_link": 1,
        "new_to_vault": 1,
    }
    payload = json.loads(result.json_path.read_text(encoding="utf-8"))
    assert payload["records"][0]["vault_match_status"] == "already_in_vault_by_music_id"
    with result.csv_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[1]["vault_match_music_id"] == "99999"


def test_tiktok_archive_url_normalization_strips_tracking_and_adds_music_variants():
    variants = normalized_tiktok_url_variants(
        "https://m.tiktok.com/h5/share/music/12345.html?u_code=tracking#fragment"
    )

    assert "https://m.tiktok.com/h5/share/music/12345.html" in variants
    assert "https://www.tiktok.com/music/-12345" in variants
