from __future__ import annotations

import contextlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

try:  # POSIX file locking; absent on Windows.
    import fcntl
except ImportError:  # pragma: no cover - non-Unix
    fcntl = None  # type: ignore[assignment]


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
    attempts: int = 0
    error: str | None = None
    note: str = ""
    # The resolved vault id, stamped when the item is marked imported, so
    # reconciliation can verify the sound's audio really landed (and re-queue it
    # if the folder is missing or empty) without re-resolving the URL.
    music_id: str | None = None


class ShortcutInboxStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock_path = path.with_name(f".{path.name}.lock")

    @contextlib.contextmanager
    def _exclusive_lock(self):
        """Serialize read-modify-write across threads AND processes.

        The desktop auto-poll worker, a user-triggered import, and the launchd
        agent can all mutate this file at once; without a cross-process lock the
        read-modify-write cycle silently drops items. ``flock`` is advisory but
        every writer goes through here, so it holds.
        """
        if fcntl is None:  # pragma: no cover - non-Unix fallback
            yield
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("w") as lock_handle:
            fcntl.flock(lock_handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_handle, fcntl.LOCK_UN)

    def add_url(self, url: str, *, source: str, relay_id: str | None = None, note: str = "") -> ShortcutInboxItem:
        url = url.strip()
        with self._exclusive_lock():
            existing = self._read_unlocked()
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
                note=(note or "").strip(),
            )
            self._write_all([*existing, item])
            return item

    def add_urls_bulk(self, entries: list[dict]) -> int:
        """Queue many URLs in a SINGLE read+write — avoids the O(n²) cost (and UI
        freeze) of calling add_url per item on a large import. Each entry is
        ``{url, source, relay_id?, note?}``; deduped by url/relay_id against
        existing items and within the batch. Returns the count actually added."""
        with self._exclusive_lock():
            existing = self._read_unlocked()
            seen_urls = {i.url for i in existing}
            seen_ids = {i.relay_id for i in existing if i.relay_id}
            new_items: list[ShortcutInboxItem] = []
            for entry in entries:
                url = str(entry.get("url") or "").strip()
                if not url:
                    continue
                relay_id = entry.get("relay_id")
                if url in seen_urls or (relay_id and relay_id in seen_ids):
                    continue
                new_items.append(
                    ShortcutInboxItem(
                        id=_stable_id(url, relay_id),
                        url=url,
                        source=str(entry.get("source") or ""),
                        status="pending",
                        created_at=_now_iso(),
                        relay_id=relay_id,
                        note=str(entry.get("note") or "").strip(),
                    )
                )
                seen_urls.add(url)
                if relay_id:
                    seen_ids.add(relay_id)
            if new_items:
                self._write_all([*existing, *new_items])
            return len(new_items)

    def all_items(self) -> list[ShortcutInboxItem]:
        # Reads are safe without the lock: _write_all swaps the file atomically,
        # so a reader always sees a fully-written prior or next snapshot.
        return self._read_unlocked()

    def _read_unlocked(self) -> list[ShortcutInboxItem]:
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

    def failed(self) -> list[ShortcutInboxItem]:
        return [item for item in self.all_items() if item.status == "failed"]

    def counts(self) -> dict[str, int]:
        """Single-pass status tally for a global progress metric.

        Returns ``{total, pending, imported, failed, other}``. File-backed, so the
        numbers survive an app restart and always reflect the true remaining work —
        exactly what a "700 of 1500" import progress bar + ETA needs. ``other`` holds
        any non-standard status (a hand-edited/corrupt row) so the buckets always sum
        to ``total`` and the bar can still reach 100%. One lock-free JSONL scan
        (~1-5 ms for ~1.5k items, ~50-200 ms for ~100k)."""
        tally = {"total": 0, "pending": 0, "imported": 0, "failed": 0, "other": 0}
        for item in self._read_unlocked():
            tally["total"] += 1
            tally[item.status if item.status in tally else "other"] += 1
        return tally

    def mark_imported(self, item_id: str, music_id: str | None = None) -> None:
        self._update(item_id, status="imported", music_id=music_id)

    def mark_failed(self, item_id: str, error: str) -> None:
        self._update(item_id, status="failed", error=error, bump_attempts=True)

    def record_failure(self, item_id: str, error: str, *, max_attempts: int = 3) -> None:
        """Increment attempts; mark failed once exhausted, otherwise keep it pending for retry.

        Reads the current attempt count and writes back under one lock so a
        concurrent poll/import cannot clobber the increment.
        """
        with self._exclusive_lock():
            updated: list[ShortcutInboxItem] = []
            changed = False
            for item in self._read_unlocked():
                if item.id == item_id:
                    attempts = item.attempts + 1
                    status = "failed" if attempts >= max_attempts else "pending"
                    updated.append(ShortcutInboxItem(**{**asdict(item), "status": status, "error": error, "attempts": attempts}))
                    changed = True
                else:
                    updated.append(item)
            if changed:
                self._write_all(updated)

    def requeue(self, item_id: str) -> bool:
        """Reset one item back to 'pending' (attempts + error cleared) so the next
        import run re-attempts it -- e.g. after fixing whatever made it fail. Failed
        items are never dropped from the queue, so this is always available. Returns
        True if the item existed."""
        with self._exclusive_lock():
            updated: list[ShortcutInboxItem] = []
            found = False
            for item in self._read_unlocked():
                if item.id == item_id:
                    updated.append(ShortcutInboxItem(**{**asdict(item), "status": "pending", "attempts": 0, "error": None}))
                    found = True
                else:
                    updated.append(item)
            if found:
                self._write_all(updated)
        return found

    def requeue_all_failed(self) -> int:
        """Reset every 'failed' item back to 'pending' for a bulk retry (after an
        upstream fix, e.g. a yt-dlp update or the auth fallback coming back). Returns
        how many were re-queued."""
        with self._exclusive_lock():
            updated: list[ShortcutInboxItem] = []
            count = 0
            for item in self._read_unlocked():
                if item.status == "failed":
                    updated.append(ShortcutInboxItem(**{**asdict(item), "status": "pending", "attempts": 0, "error": None}))
                    count += 1
                else:
                    updated.append(item)
            if count:
                self._write_all(updated)
        return count

    def _update(
        self,
        item_id: str,
        *,
        status: str,
        error: str | None = None,
        attempts: int | None = None,
        bump_attempts: bool = False,
        music_id: str | None = None,
    ) -> None:
        with self._exclusive_lock():
            updated: list[ShortcutInboxItem] = []
            for item in self._read_unlocked():
                if item.id == item_id:
                    fields = {**asdict(item), "status": status}
                    if error is not None:
                        fields["error"] = error
                    if music_id is not None:
                        fields["music_id"] = music_id
                    if attempts is not None:
                        fields["attempts"] = attempts
                    elif bump_attempts:
                        fields["attempts"] = item.attempts + 1
                    updated.append(ShortcutInboxItem(**fields))
                else:
                    updated.append(item)
            self._write_all(updated)

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
    try:
        attempts = int(data.get("attempts") or 0)
    except (TypeError, ValueError):
        attempts = 0
    error = data.get("error")
    music_id = data.get("music_id")
    return ShortcutInboxItem(
        id=item_id,
        url=url,
        source=str(data.get("source") or "unknown"),
        status=str(data.get("status") or "pending"),
        created_at=str(data.get("created_at") or _now_iso()),
        relay_id=relay_id,
        attempts=attempts,
        error=str(error) if error else None,
        note=str(data.get("note") or ""),
        music_id=str(music_id) if music_id else None,
    )
