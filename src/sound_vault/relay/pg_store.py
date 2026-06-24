"""Postgres-backed relay stores for serverless (Vercel) deployment.

Vercel functions are stateless across invocations, so the relay's inbox and
leaderboard state must live in Postgres (Neon) rather than in-memory/SQLite.
These mirror the semantics of InboxStore / LeaderboardStore exactly (same
hashing, TTLs, dataclasses) so the HTTP contract and tests are unchanged.

Connections are opened per-operation; use a pooled Neon DSN in production.
"""
from __future__ import annotations

from pathlib import Path
import secrets
import time

import psycopg

from sound_vault.relay.inbox import (
    DEFAULT_PAIR_CODE_SUBMISSION_TTL_SECONDS,
    InboxItem,
    _hash_pair_code,
    _hash_secret,
)
from sound_vault.relay.leaderboard import LeaderboardEntry, window_seconds

# Path import kept for parity with the sqlite store signature; unused here.
_ = Path


class PostgresInboxStore:
    """Drop-in replacement for InboxStore backed by Postgres."""

    def __init__(
        self,
        dsn: str,
        *,
        now=time.time,
        item_ttl_seconds: int = 24 * 60 * 60,  # 24h: relay is a pass-through, not storage
        pair_code_ttl_seconds: int = DEFAULT_PAIR_CODE_SUBMISSION_TTL_SECONDS,
    ) -> None:
        self._dsn = dsn
        self._now = now
        self._item_ttl_seconds = item_ttl_seconds
        self._pair_code_ttl_seconds = pair_code_ttl_seconds
        self._ensure_schema()

    def _connect(self):
        return psycopg.connect(self._dsn)

    def _ensure_schema(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    device_secret_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pair_codes (
                    pair_code_hash TEXT PRIMARY KEY,
                    expires_at DOUBLE PRECISION NOT NULL,
                    device_id TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS inbox_items (
                    id TEXT PRIMARY KEY,
                    pair_code_hash TEXT NOT NULL,
                    url TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL,
                    expires_at DOUBLE PRECISION NOT NULL,
                    note TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_inbox_pair ON inbox_items (pair_code_hash);
                ALTER TABLE inbox_items ADD COLUMN IF NOT EXISTS note TEXT NOT NULL DEFAULT '';
                """
            )
            conn.commit()

    def register_device(self, *, device_id: str, device_secret: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO devices (device_id, device_secret_hash) VALUES (%s, %s)
                ON CONFLICT (device_id) DO UPDATE SET device_secret_hash = EXCLUDED.device_secret_hash
                """,
                (device_id, _hash_secret(device_secret)),
            )
            conn.commit()

    def register_pair_code(self, pair_code: str, *, device_id: str) -> None:
        code_hash = _hash_pair_code(pair_code)
        expires_at = self._now() + self._pair_code_ttl_seconds
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pair_codes (pair_code_hash, expires_at, device_id) VALUES (%s, %s, %s)
                ON CONFLICT (pair_code_hash) DO UPDATE SET
                    expires_at = EXCLUDED.expires_at, device_id = EXCLUDED.device_id
                """,
                (code_hash, expires_at, device_id),
            )
            conn.commit()

    def can_accept_pair_code(self, pair_code: str) -> bool:
        code_hash = _hash_pair_code(pair_code)
        now = self._now()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT expires_at FROM pair_codes WHERE pair_code_hash = %s", (code_hash,))
            row = cur.fetchone()
            if row is None:
                return False
            if float(row[0]) < now:
                cur.execute("DELETE FROM pair_codes WHERE pair_code_hash = %s", (code_hash,))
                conn.commit()
                return False
        return True

    def submit_link(self, *, pair_code: str, url: str, source: str, note: str = "") -> InboxItem:
        now = self._now()
        item = InboxItem(
            id=f"in_{secrets.token_urlsafe(12)}",
            pair_code_hash=_hash_pair_code(pair_code),
            url=url,
            source=source,
            created_at=now,
            expires_at=now + self._item_ttl_seconds,
            note=(note or "").strip(),
        )
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO inbox_items (id, pair_code_hash, url, source, created_at, expires_at, note)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (item.id, item.pair_code_hash, item.url, item.source, item.created_at, item.expires_at, item.note),
            )
            conn.commit()
        return item

    def poll(self, *, device_id: str, device_secret: str, pair_code: str) -> list[InboxItem]:
        now = self._now()
        wanted = _hash_pair_code(pair_code)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT device_secret_hash FROM devices WHERE device_id = %s", (device_id,))
            row = cur.fetchone()
            if row is None or not secrets.compare_digest(str(row[0]), _hash_secret(device_secret)):
                return []
            cur.execute("SELECT expires_at, device_id FROM pair_codes WHERE pair_code_hash = %s", (wanted,))
            pair_row = cur.fetchone()
            if pair_row is None:
                return []
            if float(pair_row[0]) < now:
                cur.execute("DELETE FROM pair_codes WHERE pair_code_hash = %s", (wanted,))
                conn.commit()
                return []
            if str(pair_row[1]) != device_id:
                return []
            cur.execute("DELETE FROM inbox_items WHERE expires_at < %s", (now,))
            cur.execute(
                """
                DELETE FROM inbox_items
                WHERE pair_code_hash = %s AND expires_at >= %s
                RETURNING id, pair_code_hash, url, source, created_at, expires_at, note
                """,
                (wanted, now),
            )
            rows = cur.fetchall()
            conn.commit()
        return [
            InboxItem(
                id=str(r[0]), pair_code_hash=str(r[1]), url=str(r[2]), source=str(r[3]),
                created_at=float(r[4]), expires_at=float(r[5]), note=str(r[6] or ""),
            )
            for r in rows
        ]


class PostgresLeaderboardStore:
    """Drop-in replacement for LeaderboardStore backed by Postgres."""

    def __init__(self, dsn: str, *, now=time.time) -> None:
        self._dsn = dsn
        self._now = now
        self._ensure_schema()

    def _connect(self):
        return psycopg.connect(self._dsn)

    def _ensure_schema(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS save_events (
                    id TEXT PRIMARY KEY,
                    sound_id TEXT NOT NULL,
                    occurred_at DOUBLE PRECISION NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_save_events_sound ON save_events (sound_id);
                CREATE TABLE IF NOT EXISTS sound_meta (
                    sound_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    artist TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT '',
                    updated_at DOUBLE PRECISION NOT NULL DEFAULT 0
                );
                """
            )
            conn.commit()

    def reset(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM save_events")
            cur.execute("DELETE FROM sound_meta")
            conn.commit()

    def record_save(self, *, sound_id: str, platform: str = "", title: str = "", artist: str = "", occurred_at=None):
        from sound_vault.relay.leaderboard import SaveEvent

        sound_id = (sound_id or "").strip()
        ts = self._now() if occurred_at is None else occurred_at
        event = SaveEvent(
            id=f"ev_{secrets.token_urlsafe(10)}", sound_id=sound_id, platform=platform or "",
            title=title or "", artist=artist or "", occurred_at=ts,
        )
        if not sound_id:
            return event
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO save_events (id, sound_id, occurred_at) VALUES (%s, %s, %s)",
                (event.id, event.sound_id, event.occurred_at),
            )
            cur.execute(
                """
                INSERT INTO sound_meta (sound_id, title, artist, platform, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (sound_id) DO UPDATE SET
                    title = COALESCE(NULLIF(EXCLUDED.title, ''), sound_meta.title),
                    artist = COALESCE(NULLIF(EXCLUDED.artist, ''), sound_meta.artist),
                    platform = COALESCE(NULLIF(EXCLUDED.platform, ''), sound_meta.platform),
                    updated_at = EXCLUDED.updated_at
                """,
                (event.sound_id, event.title, event.artist, event.platform, event.occurred_at),
            )
            conn.commit()
        return event

    def leaderboard(self, *, limit: int = 50, window: str = "all") -> list[LeaderboardEntry]:
        limit = max(1, min(int(limit), 1000))
        seconds = window_seconds(window)
        cutoff = (self._now() - seconds) if seconds is not None else None
        sql = """
            SELECT e.sound_id, COUNT(*) AS saves,
                   COALESCE(m.title, ''), COALESCE(m.artist, ''), COALESCE(m.platform, '')
            FROM save_events e
            LEFT JOIN sound_meta m ON m.sound_id = e.sound_id
            {where}
            GROUP BY e.sound_id, m.title, m.artist, m.platform
            ORDER BY saves DESC, e.sound_id
            LIMIT %s
        """.format(where="WHERE e.occurred_at >= %s" if cutoff is not None else "")
        params = ([cutoff, limit] if cutoff is not None else [limit])
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [
            LeaderboardEntry(sound_id=str(r[0]), title=str(r[2]), artist=str(r[3]), platform=str(r[4]), saves=int(r[1]))
            for r in rows
        ]


class PostgresRateLimiter:
    """Durable fixed-window rate limiter backed by Postgres.

    The in-memory RateLimiter is per-process, so on Vercel serverless (where each
    invocation may hit a fresh instance) it doesn't actually limit anything. This
    keeps the count in a shared table keyed by (key, window_start) and increments
    atomically with an upsert, so the budget holds across all instances. Same
    interface as RateLimiter.allow(key, ...) so enforce_rate_limit is unchanged.

    Fails OPEN on any DB error: rate limiting is best-effort abuse protection and
    must never take the relay down.
    """

    def __init__(self, dsn: str, *, now=time.time) -> None:
        self._dsn = dsn
        self._now = now
        self._ensure_schema()

    def _connect(self):
        return psycopg.connect(self._dsn)

    def _ensure_schema(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS rate_limit_hits (
                    key TEXT NOT NULL,
                    window_start BIGINT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (key, window_start)
                );
                CREATE INDEX IF NOT EXISTS idx_rate_limit_window ON rate_limit_hits (window_start);
                """
            )
            conn.commit()

    def reset(self) -> None:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM rate_limit_hits")
                conn.commit()
        except psycopg.Error:
            pass

    def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        if limit <= 0:
            return True
        if window_seconds <= 0:
            window_seconds = 1
        now = int(self._now())
        window_start = (now // window_seconds) * window_seconds
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO rate_limit_hits (key, window_start, count) VALUES (%s, %s, 1)
                    ON CONFLICT (key, window_start)
                    DO UPDATE SET count = rate_limit_hits.count + 1
                    RETURNING count
                    """,
                    (key, window_start),
                )
                count = int(cur.fetchone()[0])
                # Opportunistic cleanup of expired windows (cheap, indexed).
                cur.execute(
                    "DELETE FROM rate_limit_hits WHERE window_start < %s",
                    (window_start - window_seconds * 5,),
                )
                conn.commit()
            return count <= limit
        except psycopg.Error:
            return True  # fail open — never take the relay down over rate limiting
