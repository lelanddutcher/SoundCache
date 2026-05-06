from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SoundRecord:
    music_id: str
    title: str
    artist: str
    tags: tuple[str, ...]
    status: str
    raw: dict[str, Any]
    associated_video_count: int = 0

    @property
    def search_text(self) -> str:
        return " ".join(part for part in [self.title, self.artist, *self.tags] if part).lower()


def build_index(vault_root: Path) -> list[SoundRecord]:
    catalog_path = vault_root / "catalog" / "sounds.jsonl"
    if not catalog_path.exists():
        return []
    records: list[SoundRecord] = []
    with catalog_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            data = json.loads(line)
            music_id = str(data.get("tiktok_music_id") or data.get("music_id") or data.get("id") or "")
            if not music_id:
                continue
            records.append(
                SoundRecord(
                    music_id=music_id,
                    title=str(data.get("tiktok_visible_title") or data.get("title") or ""),
                    artist=str(data.get("source_artist") or data.get("artist") or data.get("tiktok_author_or_copyright") or ""),
                    tags=tuple(data.get("tags") or []),
                    status=str(data.get("status") or "unreviewed"),
                    raw=data,
                    associated_video_count=int(data.get("associated_video_count") or 0),
                )
            )
    return records
