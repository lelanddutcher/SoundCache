from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from sound_vault.vault.indexer import SoundRecord


@dataclass(frozen=True)
class IndexStats:
    total_sounds: int
    approved_sounds: int


class IndexDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def rebuild(self, records: list[SoundRecord]) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM sounds")
            db.executemany(
                """
                INSERT INTO sounds (music_id, title, artist, tags, status, associated_video_count, search_text)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        record.music_id,
                        record.title,
                        record.artist,
                        ",".join(record.tags),
                        record.status,
                        record.associated_video_count,
                        record.search_text,
                    )
                    for record in records
                ],
            )
            db.commit()

    def search(self, query: str, *, limit: int = 200) -> list[SoundRecord]:
        normalized = query.strip().lower()
        sql = "SELECT music_id, title, artist, tags, status, associated_video_count FROM sounds"
        params: tuple[object, ...]
        if normalized:
            sql += " WHERE search_text LIKE ?"
            params = (f"%{normalized}%",)
        else:
            params = ()
        sql += " ORDER BY title COLLATE NOCASE LIMIT ?"
        params = (*params, limit)
        with sqlite3.connect(self.path) as db:
            rows = db.execute(sql, params).fetchall()
        return [
            SoundRecord(
                music_id=str(row[0]),
                title=str(row[1] or ""),
                artist=str(row[2] or ""),
                tags=tuple(tag for tag in str(row[3] or "").split(",") if tag),
                status=str(row[4] or "unreviewed"),
                associated_video_count=int(row[5] or 0),
                raw={},
            )
            for row in rows
        ]

    def stats(self) -> IndexStats:
        with sqlite3.connect(self.path) as db:
            total = int(db.execute("SELECT COUNT(*) FROM sounds").fetchone()[0])
            approved = int(db.execute("SELECT COUNT(*) FROM sounds WHERE status = 'approved'").fetchone()[0])
        return IndexStats(total_sounds=total, approved_sounds=approved)

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS sounds (
                    music_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    artist TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    status TEXT NOT NULL,
                    associated_video_count INTEGER NOT NULL DEFAULT 0,
                    search_text TEXT NOT NULL
                )
                """
            )
            db.commit()
