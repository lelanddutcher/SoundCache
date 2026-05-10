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
        try:
            self._ensure_schema()
        except sqlite3.DatabaseError:
            self._reset_cache_file()
            self._ensure_schema()

    def rebuild(self, records: list[SoundRecord]) -> None:
        deduped = {record.music_id: record for record in records}
        with self._connect() as db:
            db.execute("DELETE FROM sounds")
            db.executemany(
                """
                INSERT INTO sounds (
                    music_id, title, artist, tags, status, associated_video_count,
                    added_at, packaged_at, folder_path, local_audio_path, artwork_path,
                    evidence_image_count, usage_count, source_provider, source_confidence,
                    vault_version, canonical_url, source_music_url, music_page_title,
                    video_manifest_captured_at, transcript_text, transcript_language,
                    transcript_path, duration_seconds, search_text
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        record.music_id,
                        record.title,
                        record.artist,
                        ",".join(record.tags),
                        record.status,
                        record.associated_video_count,
                        record.added_at,
                        record.packaged_at,
                        str(record.folder_path) if record.folder_path else "",
                        str(record.local_audio_path) if record.local_audio_path else "",
                        str(record.artwork_path) if record.artwork_path else "",
                        len(record.evidence_images),
                        record.usage_count,
                        record.source_provider,
                        record.source_confidence,
                        record.vault_version,
                        record.canonical_url,
                        record.source_music_url,
                        record.music_page_title,
                        record.video_manifest_captured_at,
                        record.transcript_text,
                        record.transcript_language,
                        str(record.transcript_path) if record.transcript_path else "",
                        record.duration_seconds,
                        record.search_text,
                    )
                    for record in deduped.values()
                ],
            )
            db.commit()

    def search(
        self,
        query: str,
        *,
        limit: int | None = None,
        duration_filter: str = "all",
        media_filter: str = "all",
        status_filter: str = "all",
        usage_filter: str = "all",
    ) -> list[SoundRecord]:
        normalized = query.strip().lower()
        if limit is not None:
            limit = max(1, min(int(limit), 10000))
        sql = """
            SELECT music_id, title, artist, tags, status, associated_video_count,
                   added_at, packaged_at, folder_path, local_audio_path, artwork_path,
                   usage_count, source_provider, source_confidence, vault_version, canonical_url,
                   source_music_url, music_page_title, video_manifest_captured_at,
                   transcript_text, transcript_language, transcript_path, duration_seconds
            FROM sounds
        """
        clauses: list[str] = []
        params_list: list[object] = []
        if normalized:
            clauses.append("search_text LIKE ?")
            params_list.append(f"%{normalized}%")
        if duration_filter == "under_30":
            clauses.append("duration_seconds IS NOT NULL AND duration_seconds < 30")
        elif duration_filter == "30_plus":
            clauses.append("duration_seconds IS NOT NULL AND duration_seconds >= 30")
        if media_filter == "has_audio":
            clauses.append("COALESCE(local_audio_path, '') != ''")
        elif media_filter == "missing_audio":
            clauses.append("COALESCE(local_audio_path, '') = ''")
        elif media_filter == "has_artwork":
            clauses.append("COALESCE(artwork_path, '') != ''")
        elif media_filter == "missing_artwork":
            clauses.append("COALESCE(artwork_path, '') = ''")
        elif media_filter == "has_transcript":
            clauses.append("COALESCE(transcript_text, '') != ''")
        elif media_filter == "missing_transcript":
            clauses.append("COALESCE(transcript_text, '') = ''")
        elif media_filter == "has_videos":
            clauses.append("associated_video_count > 0")
        elif media_filter == "missing_videos":
            clauses.append("associated_video_count = 0")
        elif media_filter == "has_evidence":
            clauses.append("COALESCE(evidence_image_count, 0) > 0")
        elif media_filter == "missing_evidence":
            clauses.append("COALESCE(evidence_image_count, 0) = 0")
        if status_filter and status_filter != "all":
            clauses.append("COALESCE(NULLIF(status, ''), 'unreviewed') = ?")
            params_list.append(status_filter)
        if usage_filter == "unknown_usage":
            clauses.append("usage_count IS NULL")
        elif usage_filter == "under_1k":
            clauses.append("usage_count IS NOT NULL AND usage_count < 1000")
        elif usage_filter == "over_1k":
            clauses.append("usage_count IS NOT NULL AND usage_count >= 1000")
        elif usage_filter == "over_100k":
            clauses.append("usage_count IS NOT NULL AND usage_count >= 100000")
        elif usage_filter == "over_1m":
            clauses.append("usage_count IS NOT NULL AND usage_count >= 1000000")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY COALESCE(NULLIF(packaged_at, ''), NULLIF(added_at, ''), title) DESC, title COLLATE NOCASE LIMIT ?"
        if limit is None:
            with self._connect() as db:
                limit = int(db.execute("SELECT COUNT(*) FROM sounds").fetchone()[0] or 1)
        params = (*params_list, limit)
        with self._connect() as db:
            rows = db.execute(sql, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def get(self, music_id: str) -> SoundRecord | None:
        sql = """
            SELECT music_id, title, artist, tags, status, associated_video_count,
                   added_at, packaged_at, folder_path, local_audio_path, artwork_path,
                   usage_count, source_provider, source_confidence, vault_version, canonical_url,
                   source_music_url, music_page_title, video_manifest_captured_at,
                   transcript_text, transcript_language, transcript_path, duration_seconds
            FROM sounds
            WHERE music_id = ?
            LIMIT 1
        """
        with self._connect() as db:
            rows = db.execute(sql, (music_id,)).fetchall()
        if not rows:
            return None
        return self._row_to_record(rows[0])

    def _row_to_record(self, row: sqlite3.Row | tuple) -> SoundRecord:
        return SoundRecord(
            music_id=str(row[0]),
            title=str(row[1] or ""),
            artist=str(row[2] or ""),
            tags=tuple(tag for tag in str(row[3] or "").split(",") if tag),
            status=str(row[4] or "unreviewed"),
            associated_video_count=int(row[5] or 0),
            added_at=str(row[6] or ""),
            packaged_at=str(row[7] or ""),
            folder_path=Path(str(row[8])) if row[8] else None,
            local_audio_path=Path(str(row[9])) if row[9] else None,
            artwork_path=Path(str(row[10])) if row[10] else None,
            usage_count=int(row[11]) if row[11] is not None else None,
            source_provider=str(row[12] or ""),
            source_confidence=str(row[13] or ""),
            vault_version=str(row[14] or ""),
            canonical_url=str(row[15] or ""),
            source_music_url=str(row[16] or ""),
            music_page_title=str(row[17] or ""),
            video_manifest_captured_at=str(row[18] or ""),
            transcript_text=str(row[19] or ""),
            transcript_language=str(row[20] or ""),
            transcript_path=Path(str(row[21])) if row[21] else None,
            duration_seconds=float(row[22]) if row[22] is not None else None,
            raw={},
        )

    def stats(self) -> IndexStats:
        with self._connect() as db:
            total = int(db.execute("SELECT COUNT(*) FROM sounds").fetchone()[0])
            approved = int(db.execute("SELECT COUNT(*) FROM sounds WHERE status = 'approved'").fetchone()[0])
        return IndexStats(total_sounds=total, approved_sounds=approved)

    def status_counts(self) -> list[tuple[str, int]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT COALESCE(NULLIF(status, ''), 'unreviewed') AS queue, COUNT(*)
                FROM sounds
                GROUP BY queue
                ORDER BY COUNT(*) DESC, queue COLLATE NOCASE
                """
            ).fetchall()
        return [(str(row[0]), int(row[1])) for row in rows]

    def archive_health_counts(self) -> dict[str, int]:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved,
                    SUM(CASE WHEN COALESCE(local_audio_path, '') = '' THEN 1 ELSE 0 END) AS missing_audio,
                    SUM(CASE WHEN COALESCE(evidence_image_count, 0) = 0 THEN 1 ELSE 0 END) AS missing_evidence,
                    SUM(CASE WHEN COALESCE(artwork_path, '') = '' THEN 1 ELSE 0 END) AS missing_artwork,
                    SUM(CASE WHEN COALESCE(transcript_text, '') = '' THEN 1 ELSE 0 END) AS missing_transcript,
                    SUM(CASE WHEN COALESCE(associated_video_count, 0) = 0 THEN 1 ELSE 0 END) AS missing_associated_videos
                FROM sounds
                """
            ).fetchone()
        return {
            "total": int(row[0] or 0),
            "approved": int(row[1] or 0),
            "missing_audio": int(row[2] or 0),
            "missing_evidence": int(row[3] or 0),
            "missing_artwork": int(row[4] or 0),
            "missing_transcript": int(row[5] or 0),
            "missing_associated_videos": int(row[6] or 0),
        }

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5.0)
        db.execute("PRAGMA busy_timeout = 5000")
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA synchronous = NORMAL")
        return db

    def _ensure_schema(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS sounds (
                    music_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    artist TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    status TEXT NOT NULL,
                    associated_video_count INTEGER NOT NULL DEFAULT 0,
                    added_at TEXT NOT NULL DEFAULT '',
                    packaged_at TEXT NOT NULL DEFAULT '',
                    folder_path TEXT NOT NULL DEFAULT '',
                    local_audio_path TEXT NOT NULL DEFAULT '',
                    artwork_path TEXT NOT NULL DEFAULT '',
                    evidence_image_count INTEGER NOT NULL DEFAULT 0,
                    usage_count INTEGER,
                    source_provider TEXT NOT NULL DEFAULT '',
                    source_confidence TEXT NOT NULL DEFAULT '',
                    vault_version TEXT NOT NULL DEFAULT '',
                    canonical_url TEXT NOT NULL DEFAULT '',
                    source_music_url TEXT NOT NULL DEFAULT '',
                    music_page_title TEXT NOT NULL DEFAULT '',
                    video_manifest_captured_at TEXT NOT NULL DEFAULT '',
                    transcript_text TEXT NOT NULL DEFAULT '',
                    transcript_language TEXT NOT NULL DEFAULT '',
                    transcript_path TEXT NOT NULL DEFAULT '',
                    duration_seconds REAL,
                    search_text TEXT NOT NULL
                )
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(sounds)").fetchall()}
            migrations = {
                "added_at": "ALTER TABLE sounds ADD COLUMN added_at TEXT NOT NULL DEFAULT ''",
                "packaged_at": "ALTER TABLE sounds ADD COLUMN packaged_at TEXT NOT NULL DEFAULT ''",
                "folder_path": "ALTER TABLE sounds ADD COLUMN folder_path TEXT NOT NULL DEFAULT ''",
                "local_audio_path": "ALTER TABLE sounds ADD COLUMN local_audio_path TEXT NOT NULL DEFAULT ''",
                "artwork_path": "ALTER TABLE sounds ADD COLUMN artwork_path TEXT NOT NULL DEFAULT ''",
                "evidence_image_count": "ALTER TABLE sounds ADD COLUMN evidence_image_count INTEGER NOT NULL DEFAULT 0",
                "usage_count": "ALTER TABLE sounds ADD COLUMN usage_count INTEGER",
                "source_provider": "ALTER TABLE sounds ADD COLUMN source_provider TEXT NOT NULL DEFAULT ''",
                "source_confidence": "ALTER TABLE sounds ADD COLUMN source_confidence TEXT NOT NULL DEFAULT ''",
                "vault_version": "ALTER TABLE sounds ADD COLUMN vault_version TEXT NOT NULL DEFAULT ''",
                "canonical_url": "ALTER TABLE sounds ADD COLUMN canonical_url TEXT NOT NULL DEFAULT ''",
                "source_music_url": "ALTER TABLE sounds ADD COLUMN source_music_url TEXT NOT NULL DEFAULT ''",
                "music_page_title": "ALTER TABLE sounds ADD COLUMN music_page_title TEXT NOT NULL DEFAULT ''",
                "video_manifest_captured_at": "ALTER TABLE sounds ADD COLUMN video_manifest_captured_at TEXT NOT NULL DEFAULT ''",
                "transcript_text": "ALTER TABLE sounds ADD COLUMN transcript_text TEXT NOT NULL DEFAULT ''",
                "transcript_language": "ALTER TABLE sounds ADD COLUMN transcript_language TEXT NOT NULL DEFAULT ''",
                "transcript_path": "ALTER TABLE sounds ADD COLUMN transcript_path TEXT NOT NULL DEFAULT ''",
                "duration_seconds": "ALTER TABLE sounds ADD COLUMN duration_seconds REAL",
                "search_text": "ALTER TABLE sounds ADD COLUMN search_text TEXT NOT NULL DEFAULT ''",
            }
            for column, statement in migrations.items():
                if column not in columns:
                    db.execute(statement)
            db.execute(
                """
                UPDATE sounds
                SET search_text = LOWER(
                    TRIM(
                        COALESCE(music_id, '') || ' ' ||
                        COALESCE(title, '') || ' ' ||
                        COALESCE(artist, '') || ' ' ||
                        COALESCE(added_at, '') || ' ' ||
                        COALESCE(packaged_at, '') || ' ' ||
                        COALESCE(CAST(usage_count AS TEXT), '') || ' ' ||
                        COALESCE(source_provider, '') || ' ' ||
                        COALESCE(source_confidence, '') || ' ' ||
                        COALESCE(vault_version, '') || ' ' ||
                        COALESCE(canonical_url, '') || ' ' ||
                        COALESCE(source_music_url, '') || ' ' ||
                        COALESCE(music_page_title, '') || ' ' ||
                        COALESCE(video_manifest_captured_at, '') || ' ' ||
                        COALESCE(transcript_text, '') || ' ' ||
                        COALESCE(transcript_language, '') || ' ' ||
                        REPLACE(COALESCE(tags, ''), ',', ' ')
                    )
                )
                WHERE search_text = ''
                """
            )
            db.commit()

    def _reset_cache_file(self) -> None:
        for candidate in (self.path, self.path.with_name(f"{self.path.name}-wal"), self.path.with_name(f"{self.path.name}-shm")):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass
