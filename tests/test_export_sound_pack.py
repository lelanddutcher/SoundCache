"""Exporter logic: valid slugs + tidy titles (the odd-character edge cases)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "export_sound_pack", Path(__file__).resolve().parents[1] / "scripts" / "export_sound_pack.py"
)
export_sound_pack = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(export_sound_pack)


def _vault(tmp_path, sounds, bins):
    vault = tmp_path / "vault"
    (vault / "sounds").mkdir(parents=True)
    (vault / "catalog").mkdir(parents=True)
    for mid, title, artist, canonical in sounds:
        d = vault / "sounds" / f"{mid} - x"
        d.mkdir(parents=True)
        meta = {"tiktok_music_id": mid, "tiktok_visible_title": title, "tiktok_author_or_copyright": artist}
        if canonical is not None:
            meta["canonical_url"] = canonical
        (d / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    (vault / "catalog" / "library_collections.json").write_text(
        json.dumps({"version": 1, "favorites": [], "bins": bins}), encoding="utf-8"
    )
    return vault


def test_slugless_canonical_is_rebuilt_and_title_cleaned(tmp_path):
    vault = _vault(
        tmp_path,
        sounds=[("7466169526166637358", "♬  - Battle Sports", "Battle Sports",
                 "https://www.tiktok.com/music/-7466169526166637358?x=1")],
        bins=[{"id": "b1", "name": "Action or Sports", "music_ids": ["7466169526166637358"]}],
    )
    pack = export_sound_pack.build_pack(vault, name="P", bin_names=["Action or Sports"])
    sound = pack["packs"][0]["sounds"][0]
    assert sound["url"] == "https://www.tiktok.com/music/battle-sports-7466169526166637358"  # slug rebuilt
    assert sound["title"] == "Battle Sports"  # ♬ + leading "- " stripped
    assert "/music/-" not in sound["url"]


def test_good_canonical_url_is_preserved_minus_query(tmp_path):
    vault = _vault(
        tmp_path,
        sounds=[("123", "Cool Sound", "dj", "https://www.tiktok.com/music/cool-sound-123?share=1")],
        bins=[{"id": "b1", "name": "Vibes", "music_ids": ["123"]}],
    )
    pack = export_sound_pack.build_pack(vault, name="P", bin_names=None)
    assert pack["packs"][0]["sounds"][0]["url"] == "https://www.tiktok.com/music/cool-sound-123"


def test_all_mode_exports_every_sound(tmp_path):
    vault = _vault(
        tmp_path,
        sounds=[("1", "a", "x", None), ("2", "b", "y", None)],
        bins=[],
    )
    pack = export_sound_pack.build_pack(vault, name="All", bin_names=None, all_sounds=True)
    assert pack["sound_count"] == 2 and pack["packs"][0]["slug"] == "all-sounds"
