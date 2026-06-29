"""Tests for the folder/audio portability repair migration.

Uses a zero-width space (U+200B, category Cf) to construct a legacy non-portable
name: APFS allows it at creation, but the sanitizer drops it and other filesystems
choke on it — so it's a realistic "needs repair" case we can actually create on disk.
"""
from __future__ import annotations

import json
from pathlib import Path

from sound_vault.vault.indexer import build_index
from sound_vault.workers.folder_portability import repair_folder_portability

ZW = "​"  # zero-width space (Cf): allowed on APFS, dropped by the sanitizer


def _legacy_sound(vault: Path, music_id: str, folder_name: str, audio_name: str, *, title: str, artist: str) -> Path:
    folder = vault / "sounds" / folder_name
    folder.mkdir(parents=True)
    (folder / audio_name).write_bytes(b"\x00audio")
    meta = {
        "tiktok_music_id": music_id,
        "tiktok_visible_title": title,
        "tiktok_author_or_copyright": artist,
        "status": "ingested",
        "platform": "tiktok",
        "paths": {
            "folder": f"sounds/{folder_name}",
            "audio": f"sounds/{folder_name}/{audio_name}",
            "artwork": None,
        },
    }
    (folder / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return folder


def test_dry_run_reports_without_renaming(tmp_path):
    vault = tmp_path / "vault"
    folder_name = f"123 - so{ZW}und - cre{ZW}ator"
    folder = _legacy_sound(vault, "123", folder_name, "clip.m4a", title=f"so{ZW}und", artist=f"cre{ZW}ator")

    result = repair_folder_portability(vault, dry_run=True)

    assert result.dry_run is True
    assert result.folders_renamed == 1
    assert folder.exists()  # nothing actually renamed
    assert result.repairs[0].new_folder == "123 - sound - creator"


def test_apply_renames_folder_and_rebases_metadata(tmp_path):
    vault = tmp_path / "vault"
    folder_name = f"123 - so{ZW}und - cre{ZW}ator"
    _legacy_sound(vault, "123", folder_name, "clip.m4a", title=f"so{ZW}und", artist=f"cre{ZW}ator")

    result = repair_folder_portability(vault, dry_run=False)

    assert result.folders_renamed == 1
    new_folder = vault / "sounds" / "123 - sound - creator"
    assert new_folder.is_dir()
    assert not (vault / "sounds" / folder_name).exists()
    meta = json.loads((new_folder / "metadata.json").read_text(encoding="utf-8"))
    assert meta["paths"]["folder"] == "sounds/123 - sound - creator"
    assert meta["paths"]["audio"] == "sounds/123 - sound - creator/clip.m4a"


def test_apply_renames_non_portable_audio_file(tmp_path):
    vault = tmp_path / "vault"
    audio_name = f"so{ZW}und [TT-123] [ingested].m4a"  # non-portable audio basename
    _legacy_sound(vault, "123", "123 - sound - creator", audio_name, title="sound", artist="creator")

    result = repair_folder_portability(vault, dry_run=False)

    assert result.audio_renamed == 1
    folder = vault / "sounds" / "123 - sound - creator"
    m4as = list(folder.glob("*.m4a"))
    assert len(m4as) == 1
    from sound_vault.ingest.package import is_portable_filename

    assert is_portable_filename(m4as[0].name)
    meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    assert meta["paths"]["audio"].endswith(m4as[0].name)


def test_portable_folder_is_left_untouched(tmp_path):
    vault = tmp_path / "vault"
    _legacy_sound(vault, "123", "123 - clean title - artist", "clip.m4a", title="clean title", artist="artist")

    result = repair_folder_portability(vault, dry_run=False)

    assert result.folders_renamed == 0
    assert result.audio_renamed == 0
    assert (vault / "sounds" / "123 - clean title - artist").is_dir()


def test_kaomoji_folder_is_portable_and_left_alone(tmp_path):
    vault = tmp_path / "vault"
    name = "7209633324539693830 - sound - (~￣³￣)~"  # valid UTF-8, under NAME_MAX
    _legacy_sound(vault, "7209633324539693830", name, "clip.m4a", title="sound", artist="(~￣³￣)~")

    result = repair_folder_portability(vault, dry_run=False)
    assert result.folders_renamed == 0
    assert (vault / "sounds" / name).is_dir()


def test_indexer_still_finds_sound_after_rename(tmp_path):
    vault = tmp_path / "vault"
    (vault / "catalog").mkdir(parents=True)
    folder_name = f"7013196724253280258 - so{ZW}und - artist"
    _legacy_sound(vault, "7013196724253280258", folder_name, "clip.m4a", title=f"so{ZW}und", artist="artist")

    repair_folder_portability(vault, dry_run=False)
    records = build_index(vault, load_sidecars=False)

    assert len(records) == 1
    rec = records[0]
    assert rec.music_id == "7013196724253280258"
    # Resolved by the <music_id> -* glob → audio still found inside the renamed folder.
    assert rec.local_audio_path is not None and rec.local_audio_path.exists()
