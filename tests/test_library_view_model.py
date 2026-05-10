import json
from pathlib import Path

from sound_vault.ui.view_model import LibraryViewModel
from sound_vault.vault.indexer import SoundRecord


def test_library_view_model_preview_for_uses_exact_music_id_not_fuzzy_search(tmp_path):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    catalog.mkdir(parents=True)
    (catalog / "sounds.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "tiktok_music_id": "42",
                        "tiktok_visible_title": "Exact Sound",
                        "packaged_at": "2024-01-01T00:00:00Z",
                    }
                ),
                json.dumps(
                    {
                        "tiktok_music_id": "99",
                        "tiktok_visible_title": "Newer sound mentioning 42",
                        "packaged_at": "2026-01-01T00:00:00Z",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    vm = LibraryViewModel(vault_root=vault, index_path=tmp_path / "index.sqlite3")
    vm.rebuild_index()
    vm._records_by_id.clear()

    assert vm.preview_for("42").music_id == "42"
    assert vm.preview_for("42").title == "Exact Sound"


def test_library_view_model_rebuilds_index_and_selects_preview(tmp_path):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    catalog.mkdir(parents=True)
    (catalog / "sounds.jsonl").write_text(
        json.dumps({
            "tiktok_music_id": "42",
            "tiktok_visible_title": "Kickoff Pulse",
            "source_artist": "Stadium Lab",
            "tags": ["sports", "hype"],
            "status": "approved",
            "associated_video_count": 2,
        }) + "\n",
        encoding="utf-8",
    )

    vm = LibraryViewModel(vault_root=vault, index_path=tmp_path / "index.sqlite3")
    vm.rebuild_index()

    assert vm.stats_text() == "1 sounds • 1 approved"
    assert [row.music_id for row in vm.search("hype")] == ["42"]
    assert vm.preview_for("42").title == "Kickoff Pulse"


def test_library_view_model_search_can_return_full_library_and_duration_filters(tmp_path):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    catalog.mkdir(parents=True)
    rows = []
    for idx in range(600):
        rows.append(
            json.dumps(
                {
                    "tiktok_music_id": str(idx),
                    "tiktok_visible_title": f"♬  - Sound {idx}",
                    "tiktok_author_or_copyright": "creator",
                    "duration_seconds": 29.5 if idx % 2 == 0 else 31.0,
                    "status": "packaged_sample",
                }
            )
        )
    (catalog / "sounds.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")

    vm = LibraryViewModel(vault_root=vault, index_path=tmp_path / "index.sqlite3")
    vm.rebuild_index()

    all_rows = vm.search("")
    assert len(all_rows) == 600
    assert all(not row.title.startswith("♬") for row in all_rows)
    assert len(vm.search("", duration_filter="under_30")) == 300
    assert len(vm.search("", duration_filter="30_plus")) == 300


def test_library_view_model_media_filters_cover_editor_workflow(tmp_path):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    catalog.mkdir(parents=True)
    rows = []
    for idx in range(4):
        sound_dir = vault / "sounds" / f"{idx} - Sound {idx}"
        sound_dir.mkdir(parents=True)
        metadata = {
            "tiktok_music_id": str(idx),
            "tiktok_visible_title": f"Sound {idx}",
            "paths": {"folder": str(sound_dir)},
            "associated_video_count": 1 if idx == 3 else 0,
        }
        if idx in (0, 1, 3):
            audio = sound_dir / f"sound-{idx}.m4a"
            audio.write_bytes(b"fake")
            metadata["paths"]["audio"] = str(audio)
        if idx == 1:
            artwork = sound_dir / "artwork.jpg"
            artwork.write_bytes(b"jpg")
            metadata["paths"]["artwork"] = str(artwork)
        if idx == 2:
            (sound_dir / "transcript.json").write_text(
                json.dumps({"text": "money printer catchphrase", "language": "en"}),
                encoding="utf-8",
            )
        if idx == 3:
            (sound_dir / "associated_videos_manifest.json").write_text(
                json.dumps({"records": [{"rank": 1, "video_id": "v3"}]}),
                encoding="utf-8",
            )
        rows.append(json.dumps(metadata))
    (catalog / "sounds.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")

    vm = LibraryViewModel(vault_root=vault, index_path=tmp_path / "index.sqlite3")
    vm.rebuild_index()

    assert [r.music_id for r in vm.search("", media_filter="has_audio")] == ["3", "1", "0"]
    assert [r.music_id for r in vm.search("", media_filter="missing_audio")] == ["2"]
    assert [r.music_id for r in vm.search("", media_filter="has_artwork")] == ["1"]
    assert [r.music_id for r in vm.search("", media_filter="missing_artwork")] == ["3", "2", "0"]
    assert [r.music_id for r in vm.search("", media_filter="has_transcript")] == ["2"]
    assert [r.music_id for r in vm.search("", media_filter="missing_transcript")] == ["3", "1", "0"]
    assert [r.music_id for r in vm.search("", media_filter="has_videos")] == ["3"]
    assert [r.music_id for r in vm.search("", media_filter="missing_videos")] == ["2", "1", "0"]
    health = vm.db.archive_health_counts()
    assert health["missing_artwork"] == 3
    assert health["missing_transcript"] == 3
    assert health["missing_associated_videos"] == 3
    assert ("Missing artwork", 3, "Backfill true TikTok music-page artwork", "all", "missing_artwork") in vm.review_queue_rows()


def test_library_view_model_status_and_evidence_filters_cover_review_workflow(tmp_path):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    catalog.mkdir(parents=True)
    rows = []
    for music_id, status, evidence in [
        ("approved-1", "approved", True),
        ("review-1", "needs_review", False),
        ("unreviewed-1", "unreviewed", False),
    ]:
        sound_dir = vault / "sounds" / f"{music_id} - Sound"
        sound_dir.mkdir(parents=True)
        metadata = {
            "tiktok_music_id": music_id,
            "tiktok_visible_title": f"Sound {music_id}",
            "status": status,
            "paths": {"folder": str(sound_dir)},
        }
        if evidence:
            evidence_dir = sound_dir / "videos"
            evidence_dir.mkdir()
            (evidence_dir / f"{music_id}-music-page.jpg").write_bytes(b"jpg")
        rows.append(json.dumps(metadata))
    (catalog / "sounds.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")

    vm = LibraryViewModel(vault_root=vault, index_path=tmp_path / "index.sqlite3")
    vm.rebuild_index()

    assert [r.music_id for r in vm.search("", status_filter="approved")] == ["approved-1"]
    assert [r.music_id for r in vm.search("", status_filter="needs_review")] == ["review-1"]
    assert [r.music_id for r in vm.search("", status_filter="unreviewed")] == ["unreviewed-1"]
    assert [r.music_id for r in vm.search("", media_filter="has_evidence")] == ["approved-1"]
    assert {r.music_id for r in vm.search("", media_filter="missing_evidence")} == {"review-1", "unreviewed-1"}


def test_library_view_model_copyable_metadata_is_editor_friendly(tmp_path):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    sound_dir = vault / "sounds" / "m1 - Copyable"
    catalog.mkdir(parents=True)
    sound_dir.mkdir(parents=True)
    audio = sound_dir / "sound.m4a"
    artwork = sound_dir / "artwork.jpg"
    audio.write_bytes(b"fake")
    artwork.write_bytes(b"jpg")
    evidence_dir = sound_dir / "videos"
    evidence_dir.mkdir()
    (evidence_dir / "m1-music-page.jpg").write_bytes(b"jpg")
    (sound_dir / "transcript.json").write_text(json.dumps({"text": "clean hook", "language": "en"}), encoding="utf-8")
    (catalog / "sounds.jsonl").write_text(
        json.dumps(
            {
                "tiktok_music_id": "m1",
                "tiktok_visible_title": "Copyable Sound",
                "source_artist": "Creator",
                "status": "approved",
                "usage_count": 123456,
                "canonical_url": "https://www.tiktok.com/music/m1",
                "associated_video_count": 1,
                "paths": {"folder": str(sound_dir), "audio": str(audio), "artwork": str(artwork)},
                "tags": ["hook", "sports"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    vm = LibraryViewModel(vault_root=vault, index_path=tmp_path / "index.sqlite3")
    vm.rebuild_index()

    metadata = vm.copyable_metadata(vm.preview_for("m1"))

    assert metadata == "\n".join(
        [
            "Sound: Copyable Sound",
            "Artist/source: Creator",
            "Music ID: m1",
            "Status: approved",
            "Usage count: 123,456",
            "Canonical URL: https://www.tiktok.com/music/m1",
            f"Folder: {sound_dir}",
            f"Local audio: {audio}",
            f"Artwork: {artwork}",
            "Tags: hook, sports",
            "Quality gaps: none",
        ]
    )


def test_library_view_model_copyable_metadata_lists_quality_gaps(tmp_path):
    vm = LibraryViewModel(vault_root=tmp_path / "vault", index_path=tmp_path / "index.sqlite3")
    record = SoundRecord(
        music_id="m2",
        title="Needs Assets",
        artist="",
        tags=(),
        status="needs_review",
        raw={},
        associated_video_count=0,
        local_audio_path=None,
        artwork_path=None,
        evidence_images=(),
        transcript_text="",
    )

    metadata = vm.copyable_metadata(record)

    assert "Quality gaps: missing audio, missing evidence, missing artwork, missing transcript, missing associated videos" in metadata


def test_library_view_model_search_prefers_hydrated_records_with_associated_videos(tmp_path):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    sound_dir = vault / "sounds" / "55 - Hydrated"
    catalog.mkdir(parents=True)
    sound_dir.mkdir(parents=True)
    manifest = sound_dir / "associated_videos_manifest.json"
    clip = sound_dir / "clip.mp4"
    shot = sound_dir / "clip.jpg"
    clip.write_bytes(b"video")
    shot.write_bytes(b"jpg")
    manifest.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "rank": 1,
                        "video_id": "v55",
                        "author_handle": "creator55",
                        "downloaded_video_path": str(clip),
                        "screenshot_path": str(shot),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (catalog / "sounds.jsonl").write_text(
        json.dumps(
            {
                "tiktok_music_id": "55",
                "tiktok_visible_title": "Hydrated",
                "paths": {"folder": str(sound_dir)},
                "associated_video_count": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    vm = LibraryViewModel(vault_root=vault, index_path=tmp_path / "index.sqlite3")
    vm.rebuild_index()

    [row] = vm.search("Hydrated")
    assert row.associated_video_count == 1
    assert len(row.associated_videos) == 1
    assert row.associated_videos[0].author_handle == "creator55"
    assert vm.preview_for("55").associated_videos[0].video_path == clip


def test_backfill_artwork_missing_detection_requires_true_artwork(tmp_path):
    import importlib.util

    script_path = Path(__file__).resolve().parents[1] / "scripts" / "backfill_artwork.py"
    spec = importlib.util.spec_from_file_location("backfill_artwork", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    sound_dir = tmp_path / "123 - sound"
    sound_dir.mkdir()
    thumbnail = sound_dir / "thumbnail.jpg"
    thumbnail.write_bytes(b"not true sound artwork")
    assert module._has_artwork(sound_dir, {"paths": {"thumbnail": str(thumbnail)}}) is False

    artwork = sound_dir / "artwork.webp"
    artwork.write_bytes(b"real cover")
    assert module._has_artwork(sound_dir, {"paths": {"artwork": str(artwork)}}) is True
