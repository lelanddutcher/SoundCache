from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import secrets
import sqlite3
import time

DEFAULT_PAIR_CODE_SUBMISSION_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days


def _hash_pair_code(pair_code: str) -> str:
    return hashlib.sha256(pair_code.strip().upper().encode("utf-8")).hexdigest()


def _hash_secret(device_secret: str) -> str:
    # Device secrets are full-entropy 256-bit tokens, so a plain SHA-256 digest is
    # sufficient and lets us avoid ever storing the secret in plaintext.
    return hashlib.sha256(device_secret.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class InboxItem:
    id: str
    pair_code_hash: str
    url: str
    source: str
    created_at: float
    expires_at: float
    note: str = ""


@dataclass(frozen=True)
class RegisteredPairCode:
    expires_at: float
    device_id: str


class InboxStore:
    """Tiny relay inbox. Stores links until a paired desktop pulls them once."""

    def __init__(
        self,
        *,
        now=time.time,
        item_ttl_seconds: int = 24 * 60 * 60,  # 24h: relay is a pass-through, not storage
        pair_code_ttl_seconds: int = DEFAULT_PAIR_CODE_SUBMISSION_TTL_SECONDS,
        db_path: Path | None = None,
    ) -> None:
        self._now = now
        self._item_ttl_seconds = item_ttl_seconds
        self._pair_code_ttl_seconds = pair_code_ttl_seconds
        self._db_path = db_path
        self._devices: dict[str, str] = {}
        self._pair_codes: dict[str, RegisteredPairCode] = {}
        self._items: list[InboxItem] = []
        if self._db_path is not None:
            self._ensure_schema()
            self._load_from_db()

    def register_device(self, *, device_id: str, device_secret: str) -> None:
        # Store only the hash; the plaintext secret never touches memory or disk.
        secret_hash = _hash_secret(device_secret)
        self._devices[device_id] = secret_hash
        if self._db_path is not None:
            with self._connect() as db:
                db.execute(
                    "INSERT OR REPLACE INTO devices (device_id, device_secret) VALUES (?, ?)",
                    (device_id, secret_hash),
                )

    def register_pair_code(self, pair_code: str, *, device_id: str) -> None:
        code_hash = _hash_pair_code(pair_code)
        expires_at = self._now() + self._pair_code_ttl_seconds
        self._pair_codes[code_hash] = RegisteredPairCode(expires_at=expires_at, device_id=device_id)
        if self._db_path is not None:
            with self._connect() as db:
                db.execute(
                    """
                    INSERT OR REPLACE INTO pair_codes (pair_code_hash, expires_at, device_id)
                    VALUES (?, ?, ?)
                    """,
                    (code_hash, expires_at, device_id),
                )

    def can_accept_pair_code(self, pair_code: str) -> bool:
        code_hash = _hash_pair_code(pair_code)
        registered = self._pair_codes.get(code_hash)
        if registered is None:
            return False
        if registered.expires_at < self._now():
            self._pair_codes.pop(code_hash, None)
            if self._db_path is not None:
                with self._connect() as db:
                    db.execute("DELETE FROM pair_codes WHERE pair_code_hash = ?", (code_hash,))
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
        self._items.append(item)
        if self._db_path is not None:
            with self._connect() as db:
                db.execute(
                    """
                    INSERT OR REPLACE INTO inbox_items
                    (id, pair_code_hash, url, source, created_at, expires_at, note)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (item.id, item.pair_code_hash, item.url, item.source, item.created_at, item.expires_at, item.note),
                )
        return item

    def poll(self, *, device_id: str, device_secret: str, pair_code: str) -> list[InboxItem]:
        expected = self._devices.get(device_id)
        if expected is None or not secrets.compare_digest(expected, _hash_secret(device_secret)):
            return []
        now = self._now()
        wanted = _hash_pair_code(pair_code)
        registered = self._pair_codes.get(wanted)
        if registered is None:
            return []
        if registered.expires_at < now:
            self._pair_codes.pop(wanted, None)
            if self._db_path is not None:
                with self._connect() as db:
                    db.execute("DELETE FROM pair_codes WHERE pair_code_hash = ?", (wanted,))
            return []
        if registered.device_id != device_id:
            return []
        delivered: list[InboxItem] = []
        remaining: list[InboxItem] = []
        expired_ids: list[str] = []
        for item in self._items:
            if item.expires_at < now:
                expired_ids.append(item.id)
                continue
            if item.pair_code_hash == wanted:
                delivered.append(item)
            else:
                remaining.append(item)
        self._items = remaining
        if self._db_path is not None:
            delivered_ids = [item.id for item in delivered]
            delete_ids = delivered_ids + expired_ids
            if delete_ids:
                with self._connect() as db:
                    db.executemany("DELETE FROM inbox_items WHERE id = ?", [(item_id,) for item_id in delete_ids])
        return delivered

    def _connect(self) -> sqlite3.Connection:
        if self._db_path is None:
            raise RuntimeError("persistent inbox db is not configured")
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
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    device_secret TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pair_codes (
                    pair_code_hash TEXT PRIMARY KEY,
                    expires_at REAL NOT NULL,
                    device_id TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS inbox_items (
                    id TEXT PRIMARY KEY,
                    pair_code_hash TEXT NOT NULL,
                    url TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    note TEXT NOT NULL DEFAULT ''
                );
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(pair_codes)")}
            if "device_id" not in columns:
                db.execute("ALTER TABLE pair_codes ADD COLUMN device_id TEXT NOT NULL DEFAULT ''")
            item_columns = {row[1] for row in db.execute("PRAGMA table_info(inbox_items)")}
            if "note" not in item_columns:
                db.execute("ALTER TABLE inbox_items ADD COLUMN note TEXT NOT NULL DEFAULT ''")

    def _load_from_db(self) -> None:
        now = self._now()
        with self._connect() as db:
            db.execute("DELETE FROM pair_codes WHERE expires_at < ?", (now,))
            db.execute("DELETE FROM inbox_items WHERE expires_at < ?", (now,))
            self._devices = {
                device_id: device_secret
                for device_id, device_secret in db.execute(
                    "SELECT device_id, device_secret FROM devices"
                )
            }
            self._pair_codes = {
                code_hash: RegisteredPairCode(expires_at=expires_at, device_id=device_id)
                for code_hash, expires_at, device_id in db.execute(
                    """
                    SELECT pair_code_hash, expires_at, device_id
                    FROM pair_codes
                    WHERE device_id != ''
                    """
                )
            }
            self._items = [
                InboxItem(
                    id=str(row[0]),
                    pair_code_hash=str(row[1]),
                    url=str(row[2]),
                    source=str(row[3]),
                    created_at=float(row[4]),
                    expires_at=float(row[5]),
                    note=str(row[6] or ""),
                )
                for row in db.execute(
                    "SELECT id, pair_code_hash, url, source, created_at, expires_at, note FROM inbox_items ORDER BY created_at"
                )
            ]
