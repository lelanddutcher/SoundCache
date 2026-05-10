import json

from sound_vault.db.index_db import IndexDatabase
from sound_vault.ui.view_model import LibraryViewModel
from sound_vault.vault.indexer import build_index, inspect_catalog_stats


def test_build_index_enriches_local_audio_dates_images_and_videos(tmp_path):
    vault = tmp_path / "vault"
    sound_dir = vault / "sounds" / "123 - Archive Hit - DJ Local"
    videos_dir = sound_dir / "videos"
    videos_dir.mkdir(parents=True)
    audio = sound_dir / "Archive Hit [packaged_sample].m4a"
    audio.write_bytes(b"audio")
    music_page = videos_dir / "123-music-page.jpg"
    music_page.write_bytes(b"jpg")
    thumb = videos_dir / "01-555-creator.jpg"
    thumb.write_bytes(b"jpg")
    clip = videos_dir / "01-555-creator.mp4"
    clip.write_bytes(b"mp4")
    manifest = sound_dir / "associated_videos_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "captured_count": 1,
                "records": [
                    {
                        "rank": 1,
                        "video_id": "555",
                        "author_handle": "creator",
                        "video_url": "https://www.tiktok.com/@creator/video/555",
                        "description": "local evidence clip",
                        "downloaded_video_path": str(clip),
                        "screenshot_path": str(thumb),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (vault / "catalog").mkdir()
    (vault / "catalog" / "sounds.jsonl").write_text(
        json.dumps(
            {
                "tiktok_music_id": "123",
                "tiktok_visible_title": "Archive Hit",
                "source_artist": "DJ Local",
                "saved_at": "2026-05-01 01:02:03",
                "packaged_at": "2026-05-06T04:21:12Z",
                "paths": {"folder": str(sound_dir), "audio": str(audio)},
                "associated_video_count": 1,
                "usage_count": 4242,
                "source_provider": "oembed",
                "source_confidence": "tiktok_oembed_only",
                "vault_version": 1,
                "canonical_url": "https://www.tiktok.com/music/Archive-Hit-123",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    record = build_index(vault)[0]

    assert record.added_at == "2026-05-01 01:02:03"
    assert record.packaged_at == "2026-05-06T04:21:12Z"
    assert record.local_audio_path == audio
    assert record.folder_path == sound_dir
    assert record.evidence_images == (music_page, thumb)
    assert len(record.associated_videos) == 1
    assert record.associated_videos[0].video_id == "555"
    assert record.associated_videos[0].screenshot_path == thumb
    assert record.usage_count == 4242
    assert record.source_provider == "oembed"
    assert record.source_confidence == "tiktok_oembed_only"
    assert record.vault_version == "1"
    assert record.canonical_url == "https://www.tiktok.com/music/Archive-Hit-123"


def test_build_index_promotes_video_page_titles_capture_times_and_download_bytes(tmp_path):
    vault = tmp_path / "vault"
    sound_dir = vault / "sounds" / "222 - Walking On A Dream - Empire"
    videos_dir = sound_dir / "videos"
    videos_dir.mkdir(parents=True)
    audio = sound_dir / "walking.m4a"
    audio.write_bytes(b"audio")
    screenshot = videos_dir / "01-777-creator.jpg"
    screenshot.write_bytes(b"jpg")
    clip = videos_dir / "01-777-creator.mp4"
    clip.write_bytes(b"mp4")
    (sound_dir / "associated_videos_manifest.json").write_text(
        json.dumps(
            {
                "source_music_url": "https://www.tiktok.com/music/Walking-On-A-Dream-222",
                "music_page_title": "Empire of the Sun - Walking On A Dream | TikTok",
                "captured_at": "2026-05-06T16:33:36.712Z",
                "records": [
                    {
                        "rank": 1,
                        "video_id": "777",
                        "author_handle": "creator",
                        "video_url": "https://www.tiktok.com/@creator/video/777",
                        "page_title": "Creator clip page | TikTok",
                        "description": "evidence clip",
                        "downloaded_video_path": str(clip),
                        "screenshot_path": str(screenshot),
                        "download": {"ok": True, "bytes": 4041164},
                        "captured_at": "2026-05-06T16:33:08.314Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (vault / "catalog").mkdir()
    (vault / "catalog" / "sounds.jsonl").write_text(
        json.dumps(
            {
                "tiktok_music_id": "222",
                "tiktok_visible_title": "♬  - Empire of the Sun",
                "paths": {"folder": str(sound_dir), "audio": str(audio)},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    record = build_index(vault)[0]

    assert record.source_music_url == "https://www.tiktok.com/music/Walking-On-A-Dream-222"
    assert record.music_page_title == "Empire of the Sun - Walking On A Dream | TikTok"
    assert record.video_manifest_captured_at == "2026-05-06T16:33:36.712Z"
    assert record.associated_videos[0].page_title == "Creator clip page | TikTok"
    assert record.associated_videos[0].captured_at == "2026-05-06T16:33:08.314Z"
    assert record.associated_videos[0].download_bytes == 4041164


def test_build_index_includes_spoken_word_transcripts_in_search_metadata(tmp_path):
    vault = tmp_path / "vault"
    sound_dir = vault / "sounds" / "333 - Catchphrase Unknown - Creator"
    sound_dir.mkdir(parents=True)
    audio = sound_dir / "catchphrase.m4a"
    audio.write_bytes(b"audio")
    (sound_dir / "transcript.json").write_text(
        json.dumps(
            {
                "engine": "faster-whisper",
                "language": "en",
                "duration_seconds": 11.8,
                "text": "wait for it, this is where the money printer goes brrr",
                "segments": [
                    {"start": 0.4, "end": 2.1, "text": "wait for it"},
                    {"start": 2.1, "end": 5.8, "text": "this is where the money printer goes brrr"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (vault / "catalog").mkdir()
    (vault / "catalog" / "sounds.jsonl").write_text(
        json.dumps(
            {
                "tiktok_music_id": "333",
                "tiktok_visible_title": "Original sound - @creator",
                "paths": {"folder": str(sound_dir), "audio": str(audio)},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    [record] = build_index(vault)
    db = IndexDatabase(tmp_path / "index.sqlite3")
    db.rebuild([record])

    assert record.transcript_text == "wait for it, this is where the money printer goes brrr"
    assert record.transcript_path == sound_dir / "transcript.json"
    assert record.transcript_language == "en"
    assert "money printer goes brrr" in record.search_text
    assert db.search("money printer goes brrr")[0].music_id == "333"


def test_build_index_prefers_true_sound_artwork_over_evidence_screenshots(tmp_path):
    vault = tmp_path / "vault"
    sound_dir = vault / "sounds" / "444 - Artwork Sound - Artist"
    videos_dir = sound_dir / "videos"
    videos_dir.mkdir(parents=True)
    artwork = sound_dir / "artwork.jpg"
    artwork.write_bytes(b"jpg")
    screenshot = videos_dir / "444-music-page.jpg"
    screenshot.write_bytes(b"shot")
    audio = sound_dir / "audio.m4a"
    audio.write_bytes(b"audio")
    (vault / "catalog").mkdir()
    (vault / "catalog" / "sounds.jsonl").write_text(
        json.dumps(
            {
                "tiktok_music_id": "444",
                "tiktok_visible_title": "♬  - Artwork Sound",
                "paths": {"folder": str(sound_dir), "audio": str(audio), "artwork": str(artwork)},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    [record] = build_index(vault)
    db = IndexDatabase(tmp_path / "index.sqlite3")
    db.rebuild([record])
    [db_row] = db.search("artwork sound")

    assert record.title == "Artwork Sound"
    assert record.artwork_path == artwork
    assert record.evidence_images == (screenshot,)
    assert db_row.artwork_path == artwork



def test_index_database_round_trips_archive_context_fields(tmp_path):
    record = build_index_record("ctx", "Context Sound", "2026-05-01", "2026-05-06")
    record = record.__class__(
        music_id=record.music_id,
        title=record.title,
        artist=record.artist,
        tags=record.tags,
        status=record.status,
        raw=record.raw,
        associated_video_count=record.associated_video_count,
        added_at=record.added_at,
        packaged_at=record.packaged_at,
        usage_count=17,
        source_provider="shazam-ish",
        source_confidence="human_reviewed",
        vault_version="2",
        canonical_url="https://www.tiktok.com/music/context-ctx",
    )
    db = IndexDatabase(tmp_path / "index.sqlite3")

    db.rebuild([record])
    [db_row] = db.search("human_reviewed")

    assert db_row.usage_count == 17
    assert db_row.source_provider == "shazam-ish"
    assert db_row.source_confidence == "human_reviewed"
    assert db_row.vault_version == "2"
    assert db_row.canonical_url == "https://www.tiktok.com/music/context-ctx"


def test_build_index_deduplicates_catalog_rows_to_newest_packaged_sound(tmp_path):
    vault = tmp_path / "vault"
    (vault / "catalog").mkdir(parents=True)
    sound_dir = vault / "sounds" / "dupe - Newer Copy - Artist"
    sound_dir.mkdir(parents=True)
    audio = sound_dir / "newer.m4a"
    audio.write_bytes(b"audio")
    catalog_rows = [
        {
            "tiktok_music_id": "dupe",
            "tiktok_visible_title": "Older Copy",
            "source_artist": "Artist",
            "saved_at": "2026-05-01T00:00:00Z",
            "packaged_at": "2026-05-02T00:00:00Z",
        },
        {
            "tiktok_music_id": "dupe",
            "tiktok_visible_title": "Newer Copy",
            "source_artist": "Artist",
            "saved_at": "2026-05-03T00:00:00Z",
            "packaged_at": "2026-05-06T00:00:00Z",
            "paths": {"folder": str(sound_dir), "audio": str(audio)},
        },
    ]
    (vault / "catalog" / "sounds.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in catalog_rows),
        encoding="utf-8",
    )

    records = build_index(vault)

    assert len(records) == 1
    assert records[0].music_id == "dupe"
    assert records[0].title == "Newer Copy"
    assert records[0].packaged_at == "2026-05-06T00:00:00Z"
    assert records[0].local_audio_path == audio


def test_inspect_catalog_stats_reports_rows_unique_duplicates_and_packaged_folders(tmp_path):
    vault = tmp_path / "vault"
    (vault / "catalog").mkdir(parents=True)
    (vault / "sounds" / "111 - First - Artist").mkdir(parents=True)
    (vault / "sounds" / "222 - Second - Artist").mkdir(parents=True)
    rows = [
        {"tiktok_music_id": "111", "packaged_at": "2026-05-01T00:00:00Z"},
        {"tiktok_music_id": "111", "packaged_at": "2026-05-02T00:00:00Z"},
        {"tiktok_music_id": "222", "packaged_at": "2026-05-03T00:00:00Z"},
    ]
    (vault / "catalog" / "sounds.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows) + "not-json\n",
        encoding="utf-8",
    )

    stats = inspect_catalog_stats(vault)

    assert stats.catalog_rows == 3
    assert stats.unique_catalog_ids == 2
    assert stats.duplicate_catalog_rows == 1
    assert stats.malformed_rows == 1
    assert stats.packaged_sound_folders == 2


def test_index_database_returns_newest_packaged_sounds_first(tmp_path):
    older = build_index_record("old", "Old One", "2026-05-01 00:00:00", "2026-05-02T00:00:00Z")
    newer = build_index_record("new", "New One", "2026-05-01 00:00:00", "2026-05-06T00:00:00Z")
    db = IndexDatabase(tmp_path / "index.sqlite3")

    db.rebuild([older, newer])
    records = db.search("")

    assert [record.music_id for record in records] == ["new", "old"]
    assert records[0].packaged_at == "2026-05-06T00:00:00Z"


def test_play_target_uses_indexed_local_audio_path_from_database_rows(tmp_path):
    audio = tmp_path / "track.m4a"
    audio.write_bytes(b"audio")
    record = build_index_record("local", "Local Audio", "2026-05-01", "2026-05-06")
    record = record.__class__(
        music_id=record.music_id,
        title=record.title,
        artist=record.artist,
        tags=record.tags,
        status=record.status,
        raw={},
        associated_video_count=record.associated_video_count,
        added_at=record.added_at,
        packaged_at=record.packaged_at,
        local_audio_path=audio,
    )
    db = IndexDatabase(tmp_path / "index.sqlite3")
    db.rebuild([record])

    [db_row] = db.search("")

    assert db_row.raw == {}
    assert db_row.local_audio_path == audio
    assert LibraryViewModel.play_target_for(db_row) == audio


def test_play_target_ignores_canonical_tiktok_music_pages_without_audio_preview():
    record = build_index_record("canonical-only", "Canonical Only", "2026-05-01", "")
    record = record.__class__(
        music_id=record.music_id,
        title=record.title,
        artist=record.artist,
        tags=record.tags,
        status=record.status,
        raw={"canonical_url": "https://www.tiktok.com/music/Archive-Hit-1234567890"},
        associated_video_count=record.associated_video_count,
        added_at=record.added_at,
        packaged_at=record.packaged_at,
        canonical_url="https://www.tiktok.com/music/Archive-Hit-1234567890",
    )

    assert LibraryViewModel.play_target_for(record) is None


def test_play_target_prefers_existing_local_m4a_over_remote_preview(tmp_path):
    audio = tmp_path / "local-preview.m4a"
    audio.write_bytes(b"audio")
    record = build_index_record("both", "Both Sources", "2026-05-01", "")
    record = record.__class__(
        music_id=record.music_id,
        title=record.title,
        artist=record.artist,
        tags=record.tags,
        status=record.status,
        raw={
            "paths": {"m4a": str(audio)},
            "preview_url": "https://cdn.example.test/remote-preview.m4a",
        },
        associated_video_count=record.associated_video_count,
        added_at=record.added_at,
        packaged_at=record.packaged_at,
    )

    assert LibraryViewModel.play_target_for(record) == audio


def build_index_record(music_id, title, saved_at, packaged_at):
    from sound_vault.vault.indexer import SoundRecord

    return SoundRecord(
        music_id=music_id,
        title=title,
        artist="artist",
        tags=(),
        status="packaged_sample",
        raw={"saved_at": saved_at, "packaged_at": packaged_at},
        associated_video_count=0,
        added_at=saved_at,
        packaged_at=packaged_at,
    )
