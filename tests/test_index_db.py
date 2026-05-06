import json

from sound_vault.db.index_db import IndexDatabase
from sound_vault.vault.indexer import build_index


def test_index_database_rebuilds_and_searches_records(tmp_path):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    catalog.mkdir(parents=True)
    rows = [
        {"tiktok_music_id": "1", "tiktok_visible_title": "Stadium Hit", "source_artist": "A Band", "tags": ["football", "hype"], "status": "approved", "associated_video_count": 3},
        {"tiktok_music_id": "2", "tiktok_visible_title": "Quiet Dinner", "source_artist": "B Artist", "tags": ["restaurant", "soft"], "status": "needs_review", "associated_video_count": 1},
    ]
    (catalog / "sounds.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    records = build_index(vault)

    db = IndexDatabase(tmp_path / "index.sqlite3")
    db.rebuild(records)

    result = db.search("football")

    assert [r.music_id for r in result] == ["1"]
    assert db.stats().total_sounds == 2
    assert db.stats().approved_sounds == 1


def test_index_database_empty_query_returns_all_records(tmp_path):
    db = IndexDatabase(tmp_path / "index.sqlite3")
    db.rebuild([])

    assert db.search("") == []
