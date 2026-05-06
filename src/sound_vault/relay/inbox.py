from __future__ import annotations

from dataclasses import dataclass
import hashlib
import secrets
import time


def _hash_pair_code(pair_code: str) -> str:
    return hashlib.sha256(pair_code.strip().upper().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class InboxItem:
    id: str
    pair_code_hash: str
    url: str
    source: str
    created_at: float
    expires_at: float


class InboxStore:
    """Tiny relay inbox. Stores links until a paired desktop pulls them once."""

    def __init__(self, *, now=time.time, item_ttl_seconds: int = 7 * 24 * 60 * 60) -> None:
        self._now = now
        self._item_ttl_seconds = item_ttl_seconds
        self._devices: dict[str, str] = {}
        self._items: list[InboxItem] = []

    def register_device(self, *, device_id: str, device_secret: str) -> None:
        self._devices[device_id] = device_secret

    def submit_link(self, *, pair_code: str, url: str, source: str) -> InboxItem:
        now = self._now()
        item = InboxItem(
            id=f"in_{secrets.token_urlsafe(12)}",
            pair_code_hash=_hash_pair_code(pair_code),
            url=url,
            source=source,
            created_at=now,
            expires_at=now + self._item_ttl_seconds,
        )
        self._items.append(item)
        return item

    def poll(self, *, device_id: str, device_secret: str, pair_code: str) -> list[InboxItem]:
        if self._devices.get(device_id) != device_secret:
            return []
        now = self._now()
        wanted = _hash_pair_code(pair_code)
        delivered: list[InboxItem] = []
        remaining: list[InboxItem] = []
        for item in self._items:
            if item.expires_at < now:
                continue
            if item.pair_code_hash == wanted:
                delivered.append(item)
            else:
                remaining.append(item)
        self._items = remaining
        return delivered
