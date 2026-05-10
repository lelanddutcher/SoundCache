from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(url: str, relay_id: str | None) -> str:
    source = relay_id or url.strip()
    return "url_" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ShortcutInboxItem:
    id: str
    url: str
    source: str
    status: str
    created_at: str
    relay_id: str | None = None


class ShortcutInboxStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def add_url(self, url: str, *, source: str, relay_id: str | None = None) -> ShortcutInboxItem:
        url = url.strip()
        existing = self.all_items()
        for item in existing:
            if item.url == url or (relay_id and item.relay_id == relay_id):
                return item
        item = ShortcutInboxItem(
            id=_stable_id(url, relay_id),
            url=url,
            source=source,
            status="pending",
            created_at=_now_iso(),
            relay_id=relay_id,
        )
        self._write_all([*existing, item])
        return item

    def all_items(self) -> list[ShortcutInboxItem]:
        if not self.path.exists():
            return []
        items: list[ShortcutInboxItem] = []
        seen: set[str] = set()
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                item = _item_from_row(data)
                if item is None:
                    continue
                dedupe_key = item.relay_id or item.url or item.id
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                items.append(item)
        return items

    def pending(self) -> list[ShortcutInboxItem]:
        return [item for item in self.all_items() if item.status == "pending"]

    def mark_imported(self, item_id: str) -> None:
        items = [
            ShortcutInboxItem(**{**asdict(item), "status": "imported"}) if item.id == item_id else item
            for item in self.all_items()
        ]
        self._write_all(items)

    def _write_all(self, items: list[ShortcutInboxItem]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f".{self.path.name}.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            for item in items:
                handle.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(self.path)


def _item_from_row(data: Any) -> ShortcutInboxItem | None:
    if not isinstance(data, dict):
        return None
    url = str(data.get("url") or "").strip()
    if not url:
        return None
    relay_id = data.get("relay_id")
    if relay_id is None and str(data.get("id") or "").startswith("in_"):
        relay_id = str(data.get("id"))
    relay_id = str(relay_id) if relay_id else None
    item_id = str(data.get("id") or _stable_id(url, relay_id))
    if item_id.startswith("in_"):
        item_id = _stable_id(url, relay_id)
    return ShortcutInboxItem(
        id=item_id,
        url=url,
        source=str(data.get("source") or "unknown"),
        status=str(data.get("status") or "pending"),
        created_at=str(data.get("created_at") or _now_iso()),
        relay_id=relay_id,
    )
