"""Global "most-saved sounds" leaderboard store for the relay.

Records anonymized save events (sound id + platform + title/artist, no PII, no
device secret) and aggregates a top-N leaderboard. In-memory for tests/dev; the
same interface is backed by SQLite when a storage path is set (and by Postgres in
the hosted Vercel deployment).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import secrets
import sqlite3
import time

_WINDOWS = {
    "all": None,
    "24h": 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
    "30d": 30 * 24 * 60 * 60,
}


@dataclass(frozen=True)
class SaveEvent:
    id: str
    sound_id: str
    platform: str
    title: str
    artist: str
    occurred_at: float


@dataclass(frozen=True)
class LeaderboardEntry:
    sound_id: str
    title: str
    artist: str
    platform: str
    saves: int


def window_seconds(window: str) -> int | None:
    return _WINDOWS.get((window or "all").strip().lower(), None)


class LeaderboardStore:
    def __init__(self, *, now=time.time, db_path: Path | None = None) -> None:
        self._now = now
        self._db_path = db_path
        self._events: list[SaveEvent] = []
        self._meta: dict[str, tuple[str, str, str]] = {}  # sound_id -> (title, artist, platform)
        if self._db_path is not None:
            self._ensure_schema()
            self._load()

    def record_save(
        self,
        *,
        sound_id: str,
        platform: str = "",
        title: str = "",
        artist: str = "",
        occurred_at: float | None = None,
    ) -> SaveEvent:
        sound_id = (sound_id or "").strip()
        event = SaveEvent(
            id=f"ev_{secrets.token_urlsafe(10)}",
            sound_id=sound_id,
            platform=platform or "",
            title=title or "",
            artist=artist or "",
            occurred_at=self._now() if occurred_at is None else occurred_at,
        )
        if not sound_id:
            return event
        self._events.append(event)
        # Keep the best-known metadata; never clobber a real title/artist with blanks.
        previous = self._meta.get(sound_id)
        if previous:
            self._meta[sound_id] = (
                event.title or previous[0],
                event.artist or previous[1],
                event.platform or previous[2],
            )
        else:
            self._meta[sound_id] = (event.title, event.artist, event.platform)
        if self._db_path is not None:
            with self._connect() as db:
                db.execute(
                    "INSERT INTO save_events (id, sound_id, occurred_at) VALUES (?, ?, ?)",
                    (event.id, event.sound_id, event.occurred_at),
                )
                db.execute(
                    """
                    INSERT INTO sound_meta (sound_id, title, artist, platform, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(sound_id) DO UPDATE SET
                        title=COALESCE(NULLIF(excluded.title, ''), sound_meta.title),
                        artist=COALESCE(NULLIF(excluded.artist, ''), sound_meta.artist),
                        platform=COALESCE(NULLIF(excluded.platform, ''), sound_meta.platform),
                        updated_at=excluded.updated_at
                    """,
                    (event.sound_id, event.title, event.artist, event.platform, event.occurred_at),
                )
        return event

    def reset(self) -> None:
        self._events.clear()
        self._meta.clear()

    def leaderboard(self, *, limit: int = 50, window: str = "all") -> list[LeaderboardEntry]:
        limit = max(1, min(int(limit), 1000))
        cutoff = None
        seconds = window_seconds(window)
        if seconds is not None:
            cutoff = self._now() - seconds
        counts: dict[str, int] = {}
        for event in self._events:
            if not event.sound_id:
                continue
            if cutoff is not None and event.occurred_at < cutoff:
                continue
            counts[event.sound_id] = counts.get(event.sound_id, 0) + 1
        entries = []
        for sound_id, saves in counts.items():
            title, artist, platform = self._meta.get(sound_id, ("", "", ""))
            entries.append(
                LeaderboardEntry(sound_id=sound_id, title=title, artist=artist, platform=platform, saves=saves)
            )
        entries.sort(key=lambda entry: (-entry.saves, entry.sound_id))
        return entries[:limit]

    def _connect(self) -> sqlite3.Connection:
        if self._db_path is None:
            raise RuntimeError("persistent leaderboard db is not configured")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self._db_path, timeout=5.0)
        db.execute("PRAGMA busy_timeout = 5000")
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA synchronous = NORMAL")
        return db

    def _ensure_schema(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS save_events (
                    id TEXT PRIMARY KEY,
                    sound_id TEXT NOT NULL,
                    occurred_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_save_events_sound ON save_events (sound_id);
                CREATE TABLE IF NOT EXISTS sound_meta (
                    sound_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    artist TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL DEFAULT 0
                );
                """
            )

    def _load(self) -> None:
        with self._connect() as db:
            self._events = [
                SaveEvent(id=str(r[0]), sound_id=str(r[1]), platform="", title="", artist="", occurred_at=float(r[2]))
                for r in db.execute("SELECT id, sound_id, occurred_at FROM save_events")
            ]
            self._meta = {
                str(r[0]): (str(r[1]), str(r[2]), str(r[3]))
                for r in db.execute("SELECT sound_id, title, artist, platform FROM sound_meta")
            }
