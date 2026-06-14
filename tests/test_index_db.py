import json
import sqlite3

from sound_vault.db.index_db import IndexDatabase
from sound_vault.vault.indexer import SoundRecord, build_index


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


def test_index_database_builds_rebuildable_fts_search_cache(tmp_path):
    db = IndexDatabase(tmp_path / "index.sqlite3")
    db.rebuild(
        [
            SoundRecord(
                music_id="m1",
                title="Needle Drop",
                artist="Editor Memory",
                tags=("dialogue", "catchphrase"),
                status="approved",
                raw={},
                transcript_text="needle drop catchphrase from the chorus",
            ),
            SoundRecord(
                music_id="m2",
                title="Quiet Bed",
                artist="Editor Memory",
                tags=("soft",),
                status="approved",
                raw={},
                transcript_text="ambient underscore",
            ),
        ]
    )

    with sqlite3.connect(tmp_path / "index.sqlite3") as sqlite:
        assert sqlite.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'sounds_search'"
        ).fetchone()

    assert [record.music_id for record in db.search("need drop")] == ["m1"]
    assert [record.music_id for record in db.search("needle!!! drop???")] == ["m1"]


def test_index_database_empty_query_returns_all_records(tmp_path):
    db = IndexDatabase(tmp_path / "index.sqlite3")
    db.rebuild([])

    assert db.search("") == []


def test_index_database_recreates_corrupt_cache_file(tmp_path):
    db_path = tmp_path / "index.sqlite3"
    db_path.write_bytes(b"not a sqlite database")

    db = IndexDatabase(db_path)
    db.rebuild([
        SoundRecord(
            music_id="m1",
            title="Recovered",
            artist="Artist",
            tags=("tag",),
            status="approved",
            raw={},
        )
    ])

    assert [record.music_id for record in db.search("recovered")] == ["m1"]


def test_index_database_keeps_last_good_index_when_rebuild_fails(tmp_path):
    db = IndexDatabase(tmp_path / "index.sqlite3")
    db.rebuild(
        [
            SoundRecord(
                music_id="stable",
                title="Stable Cache",
                artist="Creator",
                tags=("safe",),
                status="approved",
                raw={},
            )
        ]
    )

    try:
        db.rebuild(
            [
                SoundRecord(
                    music_id="bad",
                    title="Bad Cache",
                    artist="Creator",
                    tags=(),
                    status="approved",
                    raw={},
                    usage_count=object(),
                )
            ]
        )
    except sqlite3.ProgrammingError:
        pass
    else:
        raise AssertionError("unsupported SQLite value should fail rebuild")

    assert [record.music_id for record in db.search("stable")] == ["stable"]
    assert db.search("bad") == []


def test_index_database_filters_status_and_evidence(tmp_path):
    db = IndexDatabase(tmp_path / "index.sqlite3")
    db.rebuild(
        [
            SoundRecord(
                music_id="approved-with-evidence",
                title="Approved",
                artist="Creator",
                tags=(),
                status="approved",
                raw={},
                evidence_images=(tmp_path / "shot.jpg",),
            ),
            SoundRecord(
                music_id="needs-review-missing-evidence",
                title="Needs Review",
                artist="Creator",
                tags=(),
                status="needs_review",
                raw={},
                evidence_images=(),
            ),
            SoundRecord(
                music_id="unreviewed-missing-evidence",
                title="Unreviewed",
                artist="Creator",
                tags=(),
                status="unreviewed",
                raw={},
                evidence_images=(),
            ),
        ]
    )

    assert [record.music_id for record in db.search("", status_filter="approved")] == ["approved-with-evidence"]
    assert [record.music_id for record in db.search("", status_filter="needs_review")] == ["needs-review-missing-evidence"]
    assert [record.music_id for record in db.search("", status_filter="unreviewed")] == ["unreviewed-missing-evidence"]
    assert [record.music_id for record in db.search("", media_filter="has_evidence")] == ["approved-with-evidence"]
    assert {record.music_id for record in db.search("", media_filter="missing_evidence")} == {
        "needs-review-missing-evidence",
        "unreviewed-missing-evidence",
    }


def test_index_database_filters_usage_popularity_tiers(tmp_path):
    db = IndexDatabase(tmp_path / "index.sqlite3")
    db.rebuild(
        [
            SoundRecord(music_id="unknown", title="Unknown", artist="Creator", tags=(), status="approved", raw={}),
            SoundRecord(music_id="small", title="Small", artist="Creator", tags=(), status="approved", raw={}, usage_count=214),
            SoundRecord(music_id="mid", title="Mid", artist="Creator", tags=(), status="approved", raw={}, usage_count=475_700),
            SoundRecord(music_id="mega", title="Mega", artist="Creator", tags=(), status="approved", raw={}, usage_count=6_900_000),
        ]
    )

    assert [record.music_id for record in db.search("", usage_filter="unknown_usage")] == ["unknown"]
    assert [record.music_id for record in db.search("", usage_filter="under_1k")] == ["small"]
    assert {record.music_id for record in db.search("", usage_filter="over_100k")} == {"mid", "mega"}
    assert [record.music_id for record in db.search("", usage_filter="over_1m")] == ["mega"]


def test_index_database_preserves_music_page_context_after_reopen(tmp_path):
    db_path = tmp_path / "index.sqlite3"
    db = IndexDatabase(db_path)
    db.rebuild(
        [
            SoundRecord(
                music_id="m1",
                title="Context Sound",
                artist="Creator",
                tags=("trend",),
                status="approved",
                raw={},
                canonical_url="https://www.tiktok.com/music/m1",
                source_music_url="https://m.tiktok.com/music/m1",
                music_page_title="Context Sound - TikTok Music",
                video_manifest_captured_at="2026-05-08T12:00:00Z",
            )
        ]
    )

    reopened = IndexDatabase(db_path)
    [record] = reopened.search("context")

    assert record.source_music_url == "https://m.tiktok.com/music/m1"
    assert record.music_page_title == "Context Sound - TikTok Music"
    assert record.video_manifest_captured_at == "2026-05-08T12:00:00Z"


def test_index_database_migrates_legacy_schema_missing_search_text(tmp_path):
    db_path = tmp_path / "index.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE sounds (
                music_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                tags TEXT NOT NULL,
                status TEXT NOT NULL,
                associated_video_count INTEGER NOT NULL DEFAULT 0,
                added_at TEXT NOT NULL DEFAULT '',
                packaged_at TEXT NOT NULL DEFAULT '',
                folder_path TEXT NOT NULL DEFAULT '',
                local_audio_path TEXT NOT NULL DEFAULT '',
                evidence_image_count INTEGER NOT NULL DEFAULT 0,
                usage_count INTEGER,
                source_provider TEXT NOT NULL DEFAULT '',
                source_confidence TEXT NOT NULL DEFAULT '',
                vault_version TEXT NOT NULL DEFAULT '',
                canonical_url TEXT NOT NULL DEFAULT ''
            )
            """
        )
        db.execute(
            """
            INSERT INTO sounds (
                music_id, title, artist, tags, status, associated_video_count,
                added_at, packaged_at, folder_path, local_audio_path,
                evidence_image_count, usage_count, source_provider, source_confidence,
                vault_version, canonical_url
            )
            VALUES ('m1', 'Legacy Sound', 'Legacy Artist', 'old,hype', 'approved', 0,
                    '', '', '', '', 0, NULL, '', '', '', '')
            """
        )

    index = IndexDatabase(db_path)

    with sqlite3.connect(db_path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(sounds)")}
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name = 'sounds_search'").fetchone()
    assert "search_text" in columns
    assert [record.music_id for record in index.search("legacy")] == ["m1"]
