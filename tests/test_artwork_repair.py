"""Shared-artwork detection and its reversible repair (sound_vault.vault.artwork_repair)."""
from __future__ import annotations

import json
from pathlib import Path

from sound_vault.vault.artwork_repair import find_shared_artwork, reject_artwork, restore_artwork

AVATAR = b"\xff\xd8 the same avatar on every page"


def _sound(root: Path, music_id: str, author: str, image: bytes, *, manifest: bool = True) -> Path:
    folder = root / "sounds" / f"{music_id} - - {author}"
    folder.mkdir(parents=True)
    (folder / "artwork.jpg").write_bytes(image)
    if manifest:
        (folder / "artwork_manifest.json").write_text("{}")
    art = f"/nas/vault/sounds/{folder.name}/artwork.jpg"
    (folder / "metadata.json").write_text(json.dumps({
        "music_id": music_id,
        "tiktok_author_or_copyright": author,
        "paths": {"artwork": art, "audio": "a.m4a"},
        "assets": [{"asset_type": "artwork", "path": art}, {"asset_type": "audio", "path": "a.m4a"}],
    }))
    return folder


def test_finds_one_image_on_many_unrelated_sounds_only(tmp_path):
    shared = [_sound(tmp_path, str(i), f"creator{i}", AVATAR) for i in range(4)]
    _sound(tmp_path, "10", "solo", b"real cover A")
    # One creator reusing their own avatar on their own sounds is legitimate.
    _sound(tmp_path, "20", "samecreator", b"own avatar")
    _sound(tmp_path, "21", "samecreator", b"own avatar")
    _sound(tmp_path, "22", "samecreator", b"own avatar")

    groups = find_shared_artwork(tmp_path)

    assert len(groups) == 1
    assert sorted(groups[0].folders) == sorted(shared) and groups[0].authors == 4


def test_reject_is_reversible(tmp_path):
    folder = _sound(tmp_path, "1", "someone", AVATAR)
    before = json.loads((folder / "metadata.json").read_text())

    assert reject_artwork(folder, reason="test", digest="abc")

    meta = json.loads((folder / "metadata.json").read_text())
    assert not (folder / "artwork.jpg").exists()
    assert (folder / "rejected-shared-artwork.jpg").read_bytes() == AVATAR
    assert not (folder / "artwork_manifest.json").exists()  # a later run can't reject a good replacement
    assert "artwork" not in meta["paths"] and meta["paths"]["audio"] == "a.m4a"
    assert [a["asset_type"] for a in meta["assets"]] == ["audio"]
    assert meta["artwork_rejected"]["reason"] == "test"
    assert not reject_artwork(folder, reason="again")  # nothing left to reject

    assert restore_artwork(folder)
    after = json.loads((folder / "metadata.json").read_text())
    assert (folder / "artwork.jpg").read_bytes() == AVATAR
    assert (folder / "artwork_manifest.json").exists()
    assert after["paths"] == before["paths"]
    assert sorted(a["asset_type"] for a in after["assets"]) == ["artwork", "audio"]
    assert "artwork_rejected" not in after


def test_indexer_no_longer_finds_rejected_artwork(tmp_path):
    from sound_vault.vault.indexer import _artwork_from_data

    folder = _sound(tmp_path, "1", "someone", AVATAR)
    reject_artwork(folder, reason="test")
    meta = json.loads((folder / "metadata.json").read_text())
    assert _artwork_from_data(folder, meta) is None
