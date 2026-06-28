#!/usr/bin/env python3
"""Export curated library bins to a portable Sound Cache "sound pack" JSON.

A sound pack is a curated LIST of sound URLs (not audio) that a new user imports
via the desktop's "Import sound pack…" button -> the URLs queue into their inbox
-> "Download & import" fetches each sound with their own TikTok session. So the
pack ships canonical /music/ links + light metadata, never copyrighted audio.

Usage:
  python scripts/export_sound_pack.py --vault "/path/to/Sound Cache" \
      --out web/packs/starter-pack.json \
      --name "Sound Cache Starter Pack" \
      --bins "Action or Sports" "Vibes" "Meme"
  (omit --bins to export every bin)
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_MUSIC_ID_RE = re.compile(r"/music/(?P<slug>.*?)-(?P<id>\d+)(?:$|[/?#])")


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "pack"


def _clean_title(raw: str) -> str:
    # TikTok stores titles like "♬ <name> - <artist>"; strip the note glyph and
    # tidy whitespace so the import preview reads cleanly.
    return " ".join(str(raw or "").replace("♬", " ").split()).lstrip("- ").strip()


def _canonical_url(meta: dict, music_id: str, *, slug_hint: str) -> str:
    url = str(meta.get("canonical_url") or "").strip().split("?")[0]
    m = _MUSIC_ID_RE.search(url + "/")
    if m and m.group("slug").strip("-"):  # canonical url with a real (non-empty) slug
        return url
    # Construct a clean /music/<slug>-<id>; TikTok matches by the trailing id, but a
    # non-empty slug avoids the slug-less "/music/-<id>" form that can 404.
    slug = _slugify(slug_hint) or f"sound-{music_id[-6:]}"
    return f"https://www.tiktok.com/music/{slug}-{music_id}"


def _index_vault(vault_root: Path) -> dict[str, dict]:
    by_id: dict[str, dict] = {}
    for meta_path in (vault_root / "sounds").glob("*/metadata.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        mid = str(meta.get("tiktok_music_id") or "").strip()
        if mid:
            by_id[mid] = meta
    return by_id


def _sound_entry(meta: dict, mid: str) -> dict:
    artist = str(meta.get("tiktok_author_or_copyright") or "").strip()
    title = _clean_title(meta.get("tiktok_visible_title"))
    return {
        "music_id": mid,
        "url": _canonical_url(meta, mid, slug_hint=title or artist),
        "title": title,
        "artist": artist,
    }


def build_pack(vault_root: Path, *, name: str, bin_names: list[str] | None, all_sounds: bool = False) -> dict:
    by_id = _index_vault(vault_root)
    packs = []
    missing: list[str] = []

    if all_sounds:
        # One big pack of the whole library — used to generate a large download test set.
        sounds = [_sound_entry(meta, mid) for mid, meta in sorted(by_id.items())]
        packs.append({"name": "All sounds", "slug": "all-sounds", "sounds": sounds})
        return {
            "sound_cache_pack_version": 1,
            "name": name,
            "description": "Full-library export (download test set).",
            "pack_count": 1,
            "sound_count": len(sounds),
            "_missing_music_ids": missing,
            "packs": packs,
        }

    collections_path = vault_root / "catalog" / "library_collections.json"
    collections = json.loads(collections_path.read_text(encoding="utf-8"))
    wanted = {n.casefold() for n in bin_names} if bin_names else None
    for bin_row in collections.get("bins") or []:
        bin_name = str(bin_row.get("name") or "").strip()
        if wanted is not None and bin_name.casefold() not in wanted:
            continue
        sounds = []
        for mid in bin_row.get("music_ids") or []:
            mid = str(mid)
            meta = by_id.get(mid)
            if meta is None:
                missing.append(mid)
                continue
            sounds.append(_sound_entry(meta, mid))
        if sounds:
            packs.append({"name": bin_name, "slug": _slugify(bin_name), "sounds": sounds})

    return {
        "sound_cache_pack_version": 1,
        "name": name,
        "description": "Curated starter sounds — import, then Download & import to fetch each with your TikTok session.",
        "pack_count": len(packs),
        "sound_count": sum(len(p["sounds"]) for p in packs),
        "_missing_music_ids": missing,  # informational; stripped from the published file
        "packs": packs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export curated bins to a Sound Cache sound pack JSON")
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--name", default="Sound Cache Starter Pack")
    parser.add_argument("--bins", nargs="*", default=None, help="bin names to include (default: all bins)")
    parser.add_argument("--all", action="store_true", help="export the entire library as one pack (download test set)")
    args = parser.parse_args(argv)

    pack = build_pack(args.vault, name=args.name, bin_names=args.bins, all_sounds=args.all)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    published = {k: v for k, v in pack.items() if not k.startswith("_")}
    args.out.write_text(json.dumps(published, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    for p in pack["packs"]:
        print(f"  {p['name']}: {len(p['sounds'])} sounds")
    print(f"total: {pack['sound_count']} sounds across {pack['pack_count']} packs")
    if pack["_missing_music_ids"]:
        print(f"WARNING: {len(pack['_missing_music_ids'])} bin members had no metadata.json (skipped): "
              f"{pack['_missing_music_ids'][:5]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
