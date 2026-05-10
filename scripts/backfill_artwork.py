#!/usr/bin/env python3
"""Backfill true TikTok sound artwork into packaged Sound Vault folders.

This is intentionally checkpointable and rate-limited. It does not recapture audio/video.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time
from typing import Any

VAULT_ROOT = Path("/nas/TikTok Sound Vault")
PROJECT = Path("/nas/Projects/Tiktok Sound Organizer")
CAPTURE_SCRIPT = PROJECT / "scripts" / "capture_music_artwork.cjs"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _folder_for(vault_root: Path, music_id: str, row: dict[str, Any]) -> Path | None:
    paths = row.get("paths")
    if isinstance(paths, dict) and paths.get("folder"):
        folder = Path(str(paths["folder"]))
        if folder.exists():
            return folder
    sounds_root = vault_root / "sounds"
    matches = sorted(p for p in sounds_root.glob(f"{music_id} -*") if p.is_dir())
    return matches[0] if matches else None


def _has_artwork(folder: Path, metadata: dict[str, Any]) -> bool:
    paths = metadata.get("paths")
    if isinstance(paths, dict):
        for key in ("artwork", "cover", "cover_art", "music_artwork"):
            value = paths.get(key)
            if value and Path(str(value)).exists():
                return True
        manifest = paths.get("artwork_manifest")
        if manifest and _read_json(Path(str(manifest))).get("ok") is True:
            return True
    for asset in metadata.get("assets", []) if isinstance(metadata.get("assets"), list) else []:
        if isinstance(asset, dict) and str(asset.get("asset_type") or "") in {"artwork", "cover_art", "sound_artwork", "music_artwork"}:
            value = asset.get("path")
            if value and Path(str(value)).exists():
                return True
    for pattern in ("artwork.*", "cover.*", "cover-art.*", "sound-artwork.*", "music-artwork.*"):
        if any(p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} for p in folder.glob(pattern)):
            return True
    return False


def iter_missing(vault_root: Path):
    catalog_path = vault_root / "catalog" / "sounds.jsonl"
    seen: set[str] = set()
    for line in catalog_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        music_id = str(row.get("tiktok_music_id") or row.get("music_id") or "")
        if not music_id or music_id in seen:
            continue
        seen.add(music_id)
        folder = _folder_for(vault_root, music_id, row)
        if not folder:
            continue
        metadata = _read_json(folder / "metadata.json")
        if _has_artwork(folder, metadata):
            continue
        music_url = str(row.get("canonical_url") or row.get("mobile_music_url") or metadata.get("canonical_url") or metadata.get("mobile_music_url") or "")
        if not music_url:
            continue
        yield music_id, music_url, folder


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill true TikTok sound artwork for Sound Vault packages.")
    parser.add_argument("--vault", type=Path, default=VAULT_ROOT)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--delay", type=float, default=3.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not CAPTURE_SCRIPT.exists():
        raise SystemExit(f"missing capture script: {CAPTURE_SCRIPT}")

    attempted = 0
    ok = 0
    for music_id, music_url, folder in iter_missing(args.vault):
        if attempted >= args.limit:
            break
        attempted += 1
        print(f"[{attempted}/{args.limit}] artwork {music_id} -> {folder.name}")
        if args.dry_run:
            print(f"  {music_url}")
            continue
        result = subprocess.run(
            ["node", str(CAPTURE_SCRIPT), music_url, str(folder), music_id],
            cwd=PROJECT,
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
        )
        if result.stdout.strip():
            print("  " + result.stdout.strip().replace("\n", "\n  "))
        if result.returncode == 0:
            ok += 1
        elif result.stderr.strip():
            print("  stderr: " + result.stderr.strip()[:500])
        time.sleep(args.delay)
    print(f"done: {ok}/{attempted} artwork captures succeeded")
    return 0 if attempted == 0 or ok > 0 or args.dry_run else 1


if __name__ == "__main__":
    raise SystemExit(main())
