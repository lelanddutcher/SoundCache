from __future__ import annotations

from dataclasses import dataclass
import json
import re
import sqlite3
from pathlib import Path

from sound_vault.vault.indexer import AssociatedVideo, SoundRecord


@dataclass(frozen=True)
class IndexStats:
    total_sounds: int
    approved_sounds: int


_FTS_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


# --- cache-fidelity serialization (raw JSON + evidence/associated-video tuples) ---
def _dump_raw(raw: dict) -> str:
    try:
        return json.dumps(raw or {}, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


def _load_raw(text: str | None) -> dict:
    if not text:
        return {}
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _dump_paths(paths) -> str:
    return json.dumps([str(p) for p in paths], ensure_ascii=False)


def _load_paths(text: str | None) -> tuple[Path, ...]:
    if not text:
        return ()
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return ()
    if not isinstance(data, list):
        return ()
    return tuple(Path(str(p)) for p in data if p)


def _dump_videos(videos) -> str:
    return json.dumps(
        [
            {
                "rank": v.rank,
                "video_id": v.video_id,
                "author_handle": v.author_handle,
                "video_url": v.video_url,
                "description": v.description,
                "video_path": str(v.video_path) if v.video_path else None,
                "screenshot_path": str(v.screenshot_path) if v.screenshot_path else None,
                "page_title": v.page_title,
                "captured_at": v.captured_at,
                "download_bytes": v.download_bytes,
            }
            for v in videos
        ],
        ensure_ascii=False,
    )


def _load_videos(text: str | None) -> tuple[AssociatedVideo, ...]:
    if not text:
        return ()
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return ()
    if not isinstance(data, list):
        return ()
    videos: list[AssociatedVideo] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        videos.append(
            AssociatedVideo(
                rank=int(entry.get("rank") or 0),
                video_id=str(entry.get("video_id") or ""),
                author_handle=str(entry.get("author_handle") or ""),
                video_url=str(entry.get("video_url") or ""),
                description=str(entry.get("description") or ""),
                video_path=Path(entry["video_path"]) if entry.get("video_path") else None,
                screenshot_path=Path(entry["screenshot_path"]) if entry.get("screenshot_path") else None,
                page_title=str(entry.get("page_title") or ""),
                captured_at=str(entry.get("captured_at") or ""),
                download_bytes=entry.get("download_bytes"),
            )
        )
    return tuple(videos)


# Column order shared by rebuild() and upsert() so they never drift apart.
_COLUMNS = (
    "music_id", "title", "artist", "tags", "status", "associated_video_count",
    "added_at", "packaged_at", "folder_path", "local_audio_path", "artwork_path",
    "evidence_image_count", "usage_count", "source_provider", "source_confidence",
    "vault_version", "canonical_url", "source_music_url", "music_page_title",
    "video_manifest_captured_at", "transcript_text", "transcript_language",
    "transcript_path", "duration_seconds", "search_text",
    "raw_json", "evidence_paths", "associated_videos",
)

# Columns selected for read (everything except search_text), in _row_to_record order.
_READ_COLUMNS = (
    "music_id", "title", "artist", "tags", "status", "associated_video_count",
    "added_at", "packaged_at", "folder_path", "local_audio_path", "artwork_path",
    "usage_count", "source_provider", "source_confidence", "vault_version",
    "canonical_url", "source_music_url", "music_page_title", "video_manifest_captured_at",
    "transcript_text", "transcript_language", "transcript_path", "duration_seconds",
    "raw_json", "evidence_paths", "associated_videos",
)


def _record_values(record: SoundRecord) -> tuple:
    return (
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
        _dump_raw(record.raw),
        _dump_paths(record.evidence_images),
        _dump_videos(record.associated_videos),
    )


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
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DROP TABLE IF EXISTS sounds_rebuild")
                db.execute("DROP TABLE IF EXISTS sounds_search_rebuild")
                self._create_sounds_table(db, "sounds_rebuild")
                self._create_search_table(db, "sounds_search_rebuild")
                self._insert_records(db, "sounds_rebuild", list(deduped.values()))
                self._insert_search_records(db, "sounds_search_rebuild", list(deduped.values()))
                db.execute("DROP TABLE sounds")
                db.execute("DROP TABLE IF EXISTS sounds_search")
                db.execute("ALTER TABLE sounds_rebuild RENAME TO sounds")
                db.execute("ALTER TABLE sounds_search_rebuild RENAME TO sounds_search")
                self._ensure_query_indexes(db)
                db.commit()
            except Exception:
                db.rollback()
                db.execute("DROP TABLE IF EXISTS sounds_rebuild")
                db.execute("DROP TABLE IF EXISTS sounds_search_rebuild")
                db.commit()
                raise

    def upsert(self, record: SoundRecord) -> None:
        """Insert or update a single sound without rebuilding the whole cache."""
        self.upsert_many([record])

    def upsert_many(self, records: list[SoundRecord]) -> None:
        deduped = {record.music_id: record for record in records}
        if not deduped:
            return
        placeholders = ", ".join("?" for _ in _COLUMNS)
        update_clause = ", ".join(f"{column}=excluded.{column}" for column in _COLUMNS if column != "music_id")
        sql = (
            f"INSERT INTO sounds ({', '.join(_COLUMNS)}) VALUES ({placeholders}) "
            f"ON CONFLICT(music_id) DO UPDATE SET {update_clause}"
        )
        with self._connect() as db:
            for record in deduped.values():
                db.execute(sql, _record_values(record))
                # keep the standalone FTS5 table in sync (delete-then-insert)
                db.execute("DELETE FROM sounds_search WHERE music_id = ?", (record.music_id,))
                db.execute(
                    "INSERT INTO sounds_search (music_id, search_text) VALUES (?, ?)",
                    (record.music_id, record.search_text),
                )
            db.commit()

    def delete_many(self, music_ids: list[str]) -> None:
        ids = [mid for mid in music_ids if mid]
        if not ids:
            return
        with self._connect() as db:
            db.executemany("DELETE FROM sounds WHERE music_id = ?", [(mid,) for mid in ids])
            db.executemany("DELETE FROM sounds_search WHERE music_id = ?", [(mid,) for mid in ids])
            db.commit()

    def _insert_records(
        self,
        db: sqlite3.Connection,
        table_name: str,
        records: list[SoundRecord],
    ) -> None:
        placeholders = ", ".join("?" for _ in _COLUMNS)
        db.executemany(
            f"INSERT INTO {table_name} ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
            [_record_values(record) for record in records],
        )

    def _insert_search_records(
        self,
        db: sqlite3.Connection,
        table_name: str,
        records: list[SoundRecord],
    ) -> None:
        db.executemany(
            f"INSERT INTO {table_name} (music_id, search_text) VALUES (?, ?)",
            [(record.music_id, record.search_text) for record in records],
        )

    def _select_columns(self, prefix: str = "sounds.") -> str:
        return ", ".join(f"{prefix}{column}" for column in _READ_COLUMNS)

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
        else:
            limit = 50000
        fts_query = self._fts_query(normalized)
        sql = f"SELECT {self._select_columns()} FROM sounds"
        clauses: list[str] = []
        params_list: list[object] = []
        if fts_query:
            sql += " JOIN sounds_search ON sounds_search.music_id = sounds.music_id"
            clauses.append("sounds_search MATCH ?")
            params_list.append(fts_query)
        if duration_filter == "under_30":
            clauses.append("sounds.duration_seconds IS NOT NULL AND sounds.duration_seconds < 30")
        elif duration_filter == "30_plus":
            clauses.append("sounds.duration_seconds IS NOT NULL AND sounds.duration_seconds >= 30")
        if media_filter == "has_audio":
            clauses.append("COALESCE(sounds.local_audio_path, '') != ''")
        elif media_filter == "missing_audio":
            clauses.append("COALESCE(sounds.local_audio_path, '') = ''")
        elif media_filter == "has_artwork":
            clauses.append("COALESCE(sounds.artwork_path, '') != ''")
        elif media_filter == "missing_artwork":
            clauses.append("COALESCE(sounds.artwork_path, '') = ''")
        elif media_filter == "has_transcript":
            clauses.append("COALESCE(sounds.transcript_text, '') != ''")
        elif media_filter == "missing_transcript":
            clauses.append("COALESCE(sounds.transcript_text, '') = ''")
        elif media_filter == "has_videos":
            clauses.append("sounds.associated_video_count > 0")
        elif media_filter == "missing_videos":
            clauses.append("sounds.associated_video_count = 0")
        elif media_filter == "has_evidence":
            clauses.append("COALESCE(sounds.evidence_image_count, 0) > 0")
        elif media_filter == "missing_evidence":
            clauses.append("COALESCE(sounds.evidence_image_count, 0) = 0")
        if status_filter and status_filter != "all":
            clauses.append("COALESCE(NULLIF(sounds.status, ''), 'unreviewed') = ?")
            params_list.append(status_filter)
        if usage_filter == "unknown_usage":
            clauses.append("sounds.usage_count IS NULL")
        elif usage_filter == "under_1k":
            clauses.append("sounds.usage_count IS NOT NULL AND sounds.usage_count < 1000")
        elif usage_filter == "over_1k":
            clauses.append("sounds.usage_count IS NOT NULL AND sounds.usage_count >= 1000")
        elif usage_filter == "over_100k":
            clauses.append("sounds.usage_count IS NOT NULL AND sounds.usage_count >= 100000")
        elif usage_filter == "over_1m":
            clauses.append("sounds.usage_count IS NOT NULL AND sounds.usage_count >= 1000000")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += """
            ORDER BY COALESCE(NULLIF(sounds.packaged_at, ''), NULLIF(sounds.added_at, ''), sounds.title) DESC,
                     sounds.title COLLATE NOCASE
            LIMIT ?
        """
        params = (*params_list, limit)
        with self._connect() as db:
            rows = db.execute(sql, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    @staticmethod
    def _fts_query(query: str) -> str:
        tokens = [token for token in _FTS_TOKEN_RE.findall(query.lower()) if token]
        if not tokens:
            return ""
        return " ".join(f"{token}*" for token in tokens)

    def get(self, music_id: str) -> SoundRecord | None:
        sql = f"SELECT {self._select_columns()} FROM sounds WHERE music_id = ? LIMIT 1"
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
            raw=_load_raw(row[23] if len(row) > 23 else None),
            evidence_images=_load_paths(row[24] if len(row) > 24 else None),
            associated_videos=_load_videos(row[25] if len(row) > 25 else None),
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
            self._create_sounds_table(db, "sounds", if_not_exists=True)
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
                "raw_json": "ALTER TABLE sounds ADD COLUMN raw_json TEXT NOT NULL DEFAULT ''",
                "evidence_paths": "ALTER TABLE sounds ADD COLUMN evidence_paths TEXT NOT NULL DEFAULT ''",
                "associated_videos": "ALTER TABLE sounds ADD COLUMN associated_videos TEXT NOT NULL DEFAULT ''",
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
            self._ensure_search_table(db)
            self._ensure_query_indexes(db)
            db.commit()

    def _create_sounds_table(
        self,
        db: sqlite3.Connection,
        table_name: str,
        *,
        if_not_exists: bool = False,
    ) -> None:
        if table_name not in {"sounds", "sounds_rebuild"}:
            raise ValueError(f"unexpected table name: {table_name}")
        existence_clause = "IF NOT EXISTS " if if_not_exists else ""
        db.execute(
            f"""
                CREATE TABLE {existence_clause}{table_name} (
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
                    search_text TEXT NOT NULL,
                    raw_json TEXT NOT NULL DEFAULT '',
                    evidence_paths TEXT NOT NULL DEFAULT '',
                    associated_videos TEXT NOT NULL DEFAULT ''
                )
                """
        )

    def _create_search_table(self, db: sqlite3.Connection, table_name: str) -> None:
        if table_name not in {"sounds_search", "sounds_search_rebuild"}:
            raise ValueError(f"unexpected search table name: {table_name}")
        db.execute(
            f"""
                CREATE VIRTUAL TABLE {table_name}
                USING fts5(music_id UNINDEXED, search_text, tokenize='unicode61')
                """
        )

    def _ensure_search_table(self, db: sqlite3.Connection) -> None:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'sounds_search' LIMIT 1"
        ).fetchone()
        if exists:
            return
        self._create_search_table(db, "sounds_search")
        db.execute(
            """
                INSERT INTO sounds_search (music_id, search_text)
                SELECT music_id, search_text FROM sounds
                """
        )

    @staticmethod
    def _ensure_query_indexes(db: sqlite3.Connection) -> None:
        db.execute("CREATE INDEX IF NOT EXISTS idx_sounds_status ON sounds(status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_sounds_usage_count ON sounds(usage_count)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_sounds_duration ON sounds(duration_seconds)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_sounds_audio ON sounds(local_audio_path)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_sounds_artwork ON sounds(artwork_path)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_sounds_videos ON sounds(associated_video_count)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_sounds_packaged_added ON sounds(packaged_at, added_at)")

    def _reset_cache_file(self) -> None:
        for candidate in (self.path, self.path.with_name(f"{self.path.name}-wal"), self.path.with_name(f"{self.path.name}-shm")):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass
