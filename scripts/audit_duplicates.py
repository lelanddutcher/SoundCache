#!/usr/bin/env python3
"""Audit likely duplicate Sound Vault sounds without deleting anything."""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

VAULT_ROOT = Path("/nas/TikTok Sound Vault")


@dataclass(frozen=True)
class DuplicateCandidate:
    group_key: str
    music_id: str
    title: str
    artist: str
    duration_seconds: float | None
    folder: str
    reason: str


def normalize_text(value: Any) -> str:
    text = str(value or "").lower().strip()
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def catalog_music_id(data: dict[str, Any]) -> str:
    return str(data.get("tiktok_music_id") or data.get("music_id") or data.get("id") or "")


def catalog_title(data: dict[str, Any]) -> str:
    return str(data.get("tiktok_visible_title") or data.get("source_title") or data.get("title") or "")


def catalog_artist(data: dict[str, Any]) -> str:
    return str(data.get("source_artist") or data.get("artist") or data.get("tiktok_author_or_copyright") or "")


def catalog_duration(data: dict[str, Any]) -> float | None:
    value = data.get("duration_seconds") or data.get("duration")
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def catalog_folder(data: dict[str, Any]) -> str:
    paths = data.get("paths")
    if isinstance(paths, dict):
        folder = paths.get("folder")
        if folder:
            return str(folder)
    return ""


def find_duplicate_candidates(vault_root: Path) -> list[DuplicateCandidate]:
    catalog_path = vault_root / "catalog" / "sounds.jsonl"
    groups: dict[str, list[dict[str, Any]]] = {}
    if not catalog_path.exists():
        return []
    with catalog_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            music_id = catalog_music_id(data)
            title = catalog_title(data)
            artist = catalog_artist(data)
            key = f"{normalize_text(title)}|{normalize_text(artist)}"
            if not key.strip("|") or not music_id:
                continue
            groups.setdefault(key, []).append(data)

    candidates: list[DuplicateCandidate] = []
    for key, rows in sorted(groups.items()):
        unique_ids = {catalog_music_id(row) for row in rows}
        if len(unique_ids) <= 1 and len(rows) <= 1:
            continue
        reason = "same normalized title+artist"
        for row in rows:
            candidates.append(
                DuplicateCandidate(
                    group_key=key,
                    music_id=catalog_music_id(row),
                    title=catalog_title(row),
                    artist=catalog_artist(row),
                    duration_seconds=catalog_duration(row),
                    folder=catalog_folder(row),
                    reason=reason,
                )
            )
    return candidates


def write_outputs(candidates: list[DuplicateCandidate], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "duplicate-candidates.json"
    csv_path = out_dir / "duplicate-candidates.csv"
    rows = [candidate.__dict__ for candidate in candidates]
    json_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["group_key", "music_id", "title", "artist", "duration_seconds", "folder", "reason"],
        )
        writer.writeheader()
        writer.writerows(rows)
    return json_path, csv_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit likely duplicate Sound Vault records; does not delete files.")
    parser.add_argument("--vault", type=Path, default=VAULT_ROOT)
    parser.add_argument("--out", type=Path, default=VAULT_ROOT / "reports")
    args = parser.parse_args()
    candidates = find_duplicate_candidates(args.vault)
    json_path, csv_path = write_outputs(candidates, args.out)
    groups = len({candidate.group_key for candidate in candidates})
    print(f"duplicate candidate rows: {len(candidates):,}")
    print(f"duplicate groups: {groups:,}")
    print(json_path)
    print(csv_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
