"""Watch the vault for external changes and trigger a debounced re-index.

The vault can change underneath the app: the headless ingest worker packages a
sound, backfill scripts enrich folders, or files land over NFS. This watches
``sounds/`` and ``catalog/`` and fires a single coalesced callback after a quiet
period, so a burst of writes triggers exactly one rebuild.
"""
from __future__ import annotations

import os
from pathlib import Path
from threading import Lock, Timer
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

_IGNORE_SUFFIXES = (".tmp", "-wal", "-shm", ".lock", ".pyc", ".part", ".ytdl")


class _VaultEventHandler(FileSystemEventHandler):
    def __init__(self, schedule: Callable[[], None]) -> None:
        self._schedule = schedule

    @staticmethod
    def is_relevant(path: str) -> bool:
        text = str(path)
        if any(text.endswith(suffix) for suffix in _IGNORE_SUFFIXES):
            return False
        base = os.path.basename(text)
        if base.startswith("."):  # atomic temp writes (e.g. .sounds.jsonl.tmp)
            return False
        if "__pycache__" in text:
            return False
        return True

    def on_any_event(self, event) -> None:
        if getattr(event, "is_directory", False):
            return
        if self.is_relevant(getattr(event, "src_path", "")):
            self._schedule()


class VaultWatcher:
    def __init__(
        self,
        vault_root: Path,
        on_change: Callable[[], None],
        *,
        debounce_seconds: float = 1.0,
        observer_factory: Callable[[], object] = Observer,
    ) -> None:
        self.vault_root = Path(vault_root)
        self._on_change = on_change
        self._debounce = debounce_seconds
        self._observer = observer_factory()
        self._handler = _VaultEventHandler(self._schedule)
        self._timer: Timer | None = None
        self._lock = Lock()

    def start(self) -> None:
        sounds = self.vault_root / "sounds"
        catalog = self.vault_root / "catalog"
        sounds.mkdir(parents=True, exist_ok=True)
        catalog.mkdir(parents=True, exist_ok=True)
        self._observer.schedule(self._handler, str(sounds), recursive=True)
        self._observer.schedule(self._handler, str(catalog), recursive=False)
        self._observer.start()

    def stop(self) -> None:
        try:
            self._observer.stop()
            self._observer.join(timeout=2)
        finally:
            with self._lock:
                if self._timer is not None:
                    self._timer.cancel()
                    self._timer = None

    def _schedule(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = Timer(self._debounce, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        try:
            self._on_change()
        except Exception:  # noqa: BLE001 - a rebuild failure must not kill the watcher thread
            pass
