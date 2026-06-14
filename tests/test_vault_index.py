import json
from pathlib import Path

from sound_vault.vault import indexer
from sound_vault.vault.indexer import _existing_path, build_index, hydrate_record, resolve_vault_root


def test_build_index_reads_catalog_jsonl(tmp_path):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    catalog.mkdir(parents=True)
    (catalog / "sounds.jsonl").write_text(
        json.dumps({
            "tiktok_music_id": "7274985708375378731",
            "tiktok_visible_title": "Geekd Up",
            "source_artist": "Young Jeezy & Fabo",
            "tags": ["football", "hype"],
            "status": "approved",
        }) + "\n",
        encoding="utf-8",
    )

    records = build_index(vault)

    assert len(records) == 1
    assert records[0].music_id == "7274985708375378731"
    assert records[0].search_text == "7274985708375378731 geekd up young jeezy & fabo football hype"


def test_build_index_drops_none_tags_inside_list(tmp_path):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    catalog.mkdir(parents=True)
    (catalog / "sounds.jsonl").write_text(
        json.dumps({"tiktok_music_id": "m1", "tags": [None, " hype ", ""]}) + "\n",
        encoding="utf-8",
    )

    records = build_index(vault)

    assert records[0].tags == ("hype",)
    assert "none" not in records[0].search_text


def _write_packaged_sound(
    vault: Path,
    music_id: str,
    *,
    title: str = "Geekd Up",
    artist: str = "Young Jeezy & Fabo",
    status: str = "packaged_sample",
) -> Path:
    sound_dir = vault / "sounds" / f"{music_id} - {title}"
    sound_dir.mkdir(parents=True)
    audio = sound_dir / "geekd-up.m4a"
    audio.write_bytes(b"fake audio")
    artwork = sound_dir / "artwork.jpg"
    artwork.write_bytes(b"fake artwork")
    (sound_dir / "metadata.json").write_text(
        json.dumps(
            {
                "tiktok_music_id": music_id,
                "tiktok_visible_title": title,
                "source_artist": artist,
                "tags": ["football", "hype"],
                "status": status,
                "paths": {"folder": "/old/machine/path", "audio": "/old/machine/path/geekd-up.m4a"},
            }
        ),
        encoding="utf-8",
    )
    return sound_dir


def test_build_index_uses_packaged_folder_metadata_when_catalog_is_missing(tmp_path):
    vault = tmp_path / "TikTok Sounds Vault"
    sound_dir = _write_packaged_sound(vault, "7274985708375378731")

    records = build_index(vault)

    assert len(records) == 1
    assert records[0].music_id == "7274985708375378731"
    assert records[0].title == "Geekd Up"
    assert records[0].folder_path == sound_dir
    assert records[0].local_audio_path == sound_dir / "geekd-up.m4a"
    assert records[0].artwork_path == sound_dir / "artwork.jpg"


def test_build_index_does_not_probe_audio_duration_by_default(monkeypatch, tmp_path):
    vault = tmp_path / "TikTok Sounds Vault"
    _write_packaged_sound(vault, "7274985708375378731")

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("ffprobe should not run during normal GUI indexing")

    monkeypatch.delenv("SOUND_VAULT_PROBE_AUDIO_DURATIONS", raising=False)
    monkeypatch.setattr(indexer.subprocess, "run", fail_if_called)

    [record] = build_index(vault)

    assert record.duration_seconds is None


def test_build_index_recurses_packaged_folders_when_catalog_exists_but_is_empty(tmp_path):
    vault = tmp_path / "TikTok Sounds Vault"
    sound_dir = _write_packaged_sound(vault, "7274985708375378731")
    catalog = vault / "catalog"
    catalog.mkdir(parents=True)
    (catalog / "sounds.jsonl").write_text("", encoding="utf-8")

    records = build_index(vault)

    assert len(records) == 1
    assert records[0].music_id == "7274985708375378731"
    assert records[0].folder_path == sound_dir


def test_build_index_includes_folder_only_records_when_catalog_is_stale(tmp_path):
    vault = tmp_path / "TikTok Sounds Vault"
    catalog = vault / "catalog"
    catalog.mkdir(parents=True)
    (catalog / "sounds.jsonl").write_text(
        json.dumps({"tiktok_music_id": "1", "tiktok_visible_title": "catalog only"}) + "\n",
        encoding="utf-8",
    )
    _write_packaged_sound(vault, "2", title="Folder Only")

    records = build_index(vault)

    assert {record.music_id for record in records} == {"1", "2"}
    folder_record = next(record for record in records if record.music_id == "2")
    assert folder_record.title == "Folder Only"


def test_build_index_fast_path_uses_catalog_without_packaged_folder_scan(tmp_path):
    vault = tmp_path / "TikTok Sounds Vault"
    catalog = vault / "catalog"
    catalog.mkdir(parents=True)
    (catalog / "sounds.jsonl").write_text(
        json.dumps({"tiktok_music_id": "1", "tiktok_visible_title": "Catalog Sound"}) + "\n",
        encoding="utf-8",
    )
    _write_packaged_sound(vault, "2", title="Folder Only")

    records = build_index(vault, load_sidecars=False)

    assert [record.music_id for record in records] == ["1"]


def test_build_index_summary_mode_reads_lightweight_sidecars_without_folder_only_scan(tmp_path):
    vault = tmp_path / "TikTok Sounds Vault"
    catalog = vault / "catalog"
    sound_dir = vault / "sounds" / "1 - Catalog Sound"
    videos_dir = sound_dir / "videos"
    catalog.mkdir(parents=True)
    videos_dir.mkdir(parents=True)
    audio = sound_dir / "sound.m4a"
    artwork = sound_dir / "artwork.jpg"
    transcript = sound_dir / "transcript.json"
    evidence = videos_dir / "01-clip.jpg"
    for path, payload in ((audio, b"audio"), (artwork, b"art"), (evidence, b"jpg")):
        path.write_bytes(payload)
    transcript.write_text(json.dumps({"text": "summary transcript hook", "language": "en"}), encoding="utf-8")
    (sound_dir / "metadata.json").write_text(
        json.dumps(
            {
                "tiktok_music_id": "1",
                "tiktok_visible_title": "Catalog Sound",
                "paths": {"folder": str(sound_dir), "audio": str(audio), "artwork": str(artwork)},
                "associated_video_count": 1,
                "hashtags": ["capcut", "filmtok"],
            }
        ),
        encoding="utf-8",
    )
    (catalog / "sounds.jsonl").write_text(
        json.dumps({"tiktok_music_id": "1", "tiktok_visible_title": "Catalog Sound"}) + "\n",
        encoding="utf-8",
    )
    _write_packaged_sound(vault, "2", title="Folder Only")

    records = build_index(vault, load_sidecars=False, sidecar_mode="summary")

    assert [record.music_id for record in records] == ["1"]
    assert records[0].local_audio_path == audio
    assert records[0].artwork_path == artwork
    assert records[0].transcript_text == "summary transcript hook"
    assert records[0].evidence_images == (evidence,)
    assert records[0].associated_video_count == 1
    assert records[0].hashtags == ("capcut", "filmtok")
    assert "#capcut" in records[0].search_text


def test_hydrate_record_rebases_associated_video_paths_into_videos_folder(tmp_path):
    vault = tmp_path / "TikTok Sounds Vault"
    catalog = vault / "catalog"
    sound_dir = vault / "sounds" / "42 - Video Sound"
    videos_dir = sound_dir / "videos"
    catalog.mkdir(parents=True)
    videos_dir.mkdir(parents=True)
    clip = videos_dir / "01-999-creator.mp4"
    shot = videos_dir / "01-999-creator.jpg"
    clip.write_bytes(b"mp4")
    shot.write_bytes(b"jpg")
    (sound_dir / "metadata.json").write_text(
        json.dumps(
            {
                "tiktok_music_id": "42",
                "tiktok_visible_title": "Video Sound",
                "paths": {"folder": str(sound_dir)},
                "associated_video_count": 1,
            }
        ),
        encoding="utf-8",
    )
    (sound_dir / "associated_videos_manifest.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "rank": 1,
                        "video_id": "999",
                        "author_handle": "creator",
                        "description": "creator clip #CapCut #EditTok",
                        "downloaded_video_path": f"/nas/TikTok Sound Vault/sounds/42 - Video Sound/videos/{clip.name}",
                        "screenshot_path": f"/nas/TikTok Sound Vault/sounds/42 - Video Sound/videos/{shot.name}",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (catalog / "sounds.jsonl").write_text(
        json.dumps({"tiktok_music_id": "42", "tiktok_visible_title": "Video Sound", "paths": {"folder": str(sound_dir)}}) + "\n",
        encoding="utf-8",
    )

    [record] = build_index(vault, load_sidecars=False, sidecar_mode="summary")
    hydrated = hydrate_record(vault, record)

    assert hydrated.associated_videos[0].video_path == clip
    assert hydrated.associated_videos[0].screenshot_path == shot
    assert hydrated.associated_videos[0].hashtags == ("capcut", "edittok")
    assert hydrated.hashtags == ("capcut", "edittok")
    assert "#capcut" in hydrated.search_text


def test_build_index_rebases_stale_absolute_asset_paths_to_selected_vault(tmp_path):
    vault = tmp_path / "Copied TikTok Sound Vault"
    sound_dir = vault / "sounds" / "42 - Folder Only"
    sound_dir.mkdir(parents=True)
    audio = sound_dir / "folder-audio.m4a"
    audio.write_bytes(b"audio")
    artwork = sound_dir / "artwork.jpg"
    artwork.write_bytes(b"art")
    transcript = sound_dir / "transcript.json"
    transcript.write_text(json.dumps({"text": "copied vault words", "language": "en"}), encoding="utf-8")
    (sound_dir / "metadata.json").write_text(
        json.dumps(
            {
                "tiktok_music_id": "42",
                "tiktok_visible_title": "Folder Only",
                "paths": {
                    "folder": "/nas/TikTok Sound Vault/sounds/42 - Folder Only",
                    "audio": "/nas/TikTok Sound Vault/sounds/42 - Folder Only/folder-audio.m4a",
                    "artwork": "/nas/TikTok Sound Vault/sounds/42 - Folder Only/artwork.jpg",
                    "transcript": "/nas/TikTok Sound Vault/sounds/42 - Folder Only/transcript.json",
                },
            }
        ),
        encoding="utf-8",
    )

    [record] = build_index(vault)

    assert record.folder_path == sound_dir
    assert record.local_audio_path == audio
    assert record.artwork_path == artwork
    assert record.transcript_path == transcript
    assert record.transcript_text == "copied vault words"


def test_build_index_accepts_user_selecting_the_sounds_folder(tmp_path):
    vault = tmp_path / "TikTok Sounds Vault"
    sound_dir = vault / "sounds" / "42 - Kickoff Pulse"
    sound_dir.mkdir(parents=True)
    (sound_dir / "metadata.json").write_text(
        json.dumps({"tiktok_music_id": "42", "tiktok_visible_title": "Kickoff Pulse"}),
        encoding="utf-8",
    )

    assert resolve_vault_root(vault / "sounds") == vault
    assert [record.music_id for record in build_index(vault / "sounds")] == ["42"]


def test_existing_path_ignores_unstatable_paths(monkeypatch):
    def raise_os_error(self):
        raise OSError("path cannot be statted")

    monkeypatch.setattr(Path, "exists", raise_os_error)

    assert _existing_path("/tmp/unstatable") is None


def test_existing_path_ignores_invalid_paths():
    assert _existing_path("bad\x00path") is None
