import json

from sound_vault.vault.indexer import build_index
from sound_vault.workers.path_migration import migrate_vault_paths, to_relative_vault_path


def test_to_relative_vault_path():
    assert to_relative_vault_path("/path/to/Sound Cache/sounds/1 - x/a.m4a") == "sounds/1 - x/a.m4a"
    assert to_relative_vault_path("/path/to/Sound Cache/catalog/sounds.jsonl") == "catalog/sounds.jsonl"
    assert to_relative_vault_path("sounds/1 - x/a.m4a") == "sounds/1 - x/a.m4a"  # already relative
    assert to_relative_vault_path("https://www.tiktok.com/music/x-1") == "https://www.tiktok.com/music/x-1"
    assert to_relative_vault_path("") == ""


def _seed_absolute(tmp_path):
    folder = tmp_path / "sounds" / "1 - Song - Artist"
    folder.mkdir(parents=True)
    (folder / "song.m4a").write_bytes(b"\x00audio")
    abs_folder = "/path/to/Sound Cache/sounds/1 - Song - Artist"
    metadata = {
        "tiktok_music_id": "1",
        "tiktok_visible_title": "Song",
        "tiktok_author_or_copyright": "Artist",
        "status": "ingested",
        "paths": {"folder": abs_folder, "audio": f"{abs_folder}/song.m4a"},
        "assets": [{"asset_type": "artwork", "path": f"{abs_folder}/artwork.jpg"}],
    }
    (folder / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    catalog = tmp_path / "catalog" / "sounds.jsonl"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(json.dumps(metadata) + "\n", encoding="utf-8")
    return catalog, folder


def test_dry_run_reports_but_changes_nothing(tmp_path):
    catalog, folder = _seed_absolute(tmp_path)
    before = catalog.read_text(encoding="utf-8")
    result = migrate_vault_paths(tmp_path, dry_run=True)
    assert result.dry_run is True
    assert result.paths_changed > 0
    assert result.rows_changed == 1
    assert catalog.read_text(encoding="utf-8") == before  # untouched


def test_apply_rewrites_to_relative_and_stays_indexable(tmp_path):
    catalog, folder = _seed_absolute(tmp_path)
    result = migrate_vault_paths(tmp_path, dry_run=False)
    assert result.dry_run is False
    assert result.rows_changed == 1

    row = json.loads(catalog.read_text(encoding="utf-8").splitlines()[0])
    assert row["paths"]["folder"] == "sounds/1 - Song - Artist"
    assert row["paths"]["audio"] == "sounds/1 - Song - Artist/song.m4a"
    assert row["assets"][0]["path"] == "sounds/1 - Song - Artist/artwork.jpg"

    meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    assert meta["paths"]["folder"] == "sounds/1 - Song - Artist"

    records = build_index(tmp_path)
    assert records[0].music_id == "1"
    assert records[0].local_audio_path is not None


def test_apply_is_idempotent(tmp_path):
    catalog, _ = _seed_absolute(tmp_path)
    migrate_vault_paths(tmp_path, dry_run=False)
    second = migrate_vault_paths(tmp_path, dry_run=False)
    assert second.paths_changed == 0  # nothing left to convert
