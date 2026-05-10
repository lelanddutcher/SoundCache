from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from sound_vault.db.index_db import IndexDatabase
from sound_vault.ingest.shortcut_inbox import ShortcutInboxItem, ShortcutInboxStore
from sound_vault.relay.client import RelayClient, RelayInboxItem, _default_get_json
from sound_vault.vault.indexer import CatalogStats, SoundRecord, build_index, inspect_catalog_stats
from sound_vault.workers.dedupe_review import DuplicateDecisionStore, DuplicateReviewGroup, load_duplicate_review_groups


class LibraryViewModel:
    def __init__(self, *, vault_root: Path, index_path: Path, inbox_path: Path | None = None) -> None:
        self.vault_root = vault_root
        self.index_path = index_path
        self.inbox_path = inbox_path or vault_root / "inbox" / "urls" / "shortcut-inbox.jsonl"
        self.db = IndexDatabase(index_path)
        self.inbox = ShortcutInboxStore(self.inbox_path)
        self.duplicate_report_path = vault_root / "reports" / "duplicate-candidates.json"
        self.duplicate_decisions = DuplicateDecisionStore(vault_root / "reports" / "duplicate-decisions.jsonl")
        self._records_by_id: dict[str, SoundRecord] = {}
        self._catalog_stats = CatalogStats(0, 0, 0, 0, 0)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sound-vault-index")
        self._lock = Lock()

    def rebuild_index(self) -> int:
        catalog_stats = inspect_catalog_stats(self.vault_root)
        records = build_index(self.vault_root)
        with self._lock:
            self._catalog_stats = catalog_stats
            self._records_by_id = {record.music_id: record for record in records}
            self.db.rebuild(records)
        return len(records)

    def rebuild_index_async(self) -> Future[int]:
        return self._executor.submit(self.rebuild_index)

    @staticmethod
    def play_target_for(record: SoundRecord) -> Path | str | None:
        if record.local_audio_path and record.local_audio_path.exists():
            return record.local_audio_path
        paths = record.raw.get("paths") if isinstance(record.raw, dict) else None
        if isinstance(paths, dict):
            for key in ("audio", "preview", "preview_audio", "m4a", "file"):
                value = paths.get(key)
                if value:
                    path = Path(str(value))
                    if path.exists():
                        return path
        for key in ("preview_url", "audio_url", "media_url"):
            value = record.raw.get(key) if isinstance(record.raw, dict) else None
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
        return None

    def search(
        self,
        query: str,
        *,
        duration_filter: str = "all",
        media_filter: str = "all",
        status_filter: str = "all",
        usage_filter: str = "all",
    ) -> list[SoundRecord]:
        records = self.db.search(
            query,
            duration_filter=duration_filter,
            media_filter=media_filter,
            status_filter=status_filter,
            usage_filter=usage_filter,
        )
        hydrated: list[SoundRecord] = []
        for record in records:
            cached = self._records_by_id.get(record.music_id)
            if cached is not None:
                hydrated.append(cached)
                continue
            self._records_by_id[record.music_id] = record
            hydrated.append(record)
        return hydrated

    def preview_for(self, music_id: str) -> SoundRecord:
        if music_id not in self._records_by_id:
            record = self.db.get(music_id)
            if record is None:
                raise KeyError(music_id)
            self._records_by_id[record.music_id] = record
        return self._records_by_id[music_id]

    def stats_text(self) -> str:
        stats = self.db.stats()
        return f"{stats.total_sounds:,} sounds • {stats.approved_sounds:,} approved"

    def catalog_stats(self) -> CatalogStats:
        return self._catalog_stats

    def catalog_stats_text(self) -> str:
        catalog = self._catalog_stats
        return (
            f"Catalog: {catalog.catalog_rows:,} rows / {catalog.unique_catalog_ids:,} unique IDs\n"
            f"{catalog.duplicate_catalog_rows:,} duplicate rows • "
            f"{catalog.packaged_sound_folders:,} packaged folders"
        )

    def add_shortcut_url(self, url: str, *, source: str, relay_id: str | None = None) -> ShortcutInboxItem:
        return self.inbox.add_url(url, source=source, relay_id=relay_id)

    def poll_relay_inbox(
        self,
        *,
        base_url: str,
        pair_code: str,
        device_id: str,
        device_secret: str,
        get_json: Callable[..., dict[str, Any]] = _default_get_json,
    ) -> list[RelayInboxItem]:
        if not all(value.strip() for value in (base_url, pair_code, device_id, device_secret)):
            return []
        client = RelayClient(
            base_url=base_url,
            pair_code=pair_code,
            device_id=device_id,
            device_secret=device_secret,
            get_json=get_json,
        )
        return client.poll_to_inbox(self.inbox_path)

    def pending_inbox(self) -> list[ShortcutInboxItem]:
        return self.inbox.pending()

    def inbox_text(self) -> str:
        count = len(self.pending_inbox())
        suffix = "link" if count == 1 else "links"
        return f"{count:,} pending Shortcut {suffix}"

    def review_queue_rows(self) -> list[tuple[str, int, str, str, str]]:
        health = self.db.archive_health_counts()
        rows = [(status, count, "Review status", status, "all") for status, count in self.db.status_counts()]
        duplicate_count = len(self.duplicate_review_groups())
        if duplicate_count:
            rows.append(("Potential duplicates", duplicate_count, "Open duplicate review tab", "all", "all"))
        if health["missing_audio"]:
            rows.append(("Missing local audio", health["missing_audio"], "Add or repair audio file", "all", "missing_audio"))
        if health["missing_evidence"]:
            rows.append(("Missing evidence", health["missing_evidence"], "Add screenshots or source proof", "all", "missing_evidence"))
        if health["missing_artwork"]:
            rows.append(("Missing artwork", health["missing_artwork"], "Backfill true TikTok music-page artwork", "all", "missing_artwork"))
        if health["missing_transcript"]:
            rows.append(("Missing transcripts", health["missing_transcript"], "Run local ASR sidecar worker", "all", "missing_transcript"))
        if health["missing_associated_videos"]:
            rows.append(("Missing associated videos", health["missing_associated_videos"], "Backfill example/trend video evidence", "all", "missing_videos"))
        return rows or [("No review items", 0, "Archive is empty or fully reviewed", "all", "all")]

    def archive_health_rows(self) -> list[tuple[str, str]]:
        health = self.db.archive_health_counts()
        catalog = self.catalog_stats()
        return [
            ("Vault root", str(self.vault_root)),
            ("Index database", str(self.index_path)),
            ("Indexed unique sounds", f"{health['total']:,}"),
            ("Catalog rows", f"{catalog.catalog_rows:,}"),
            ("Catalog unique IDs", f"{catalog.unique_catalog_ids:,}"),
            ("Duplicate catalog rows", f"{catalog.duplicate_catalog_rows:,}"),
            ("Malformed catalog rows", f"{catalog.malformed_rows:,}"),
            ("Packaged sound folders", f"{catalog.packaged_sound_folders:,}"),
            ("Approved sounds", f"{health['approved']:,}"),
            ("Missing local audio", f"{health['missing_audio']:,}"),
            ("Missing evidence", f"{health['missing_evidence']:,}"),
            ("Missing artwork", f"{health['missing_artwork']:,}"),
            ("Missing transcripts", f"{health['missing_transcript']:,}"),
            ("Missing associated videos", f"{health['missing_associated_videos']:,}"),
            ("Pending inbox links", f"{len(self.pending_inbox()):,}"),
        ]

    def copyable_metadata(self, record: SoundRecord) -> str:
        gaps = []
        if self.play_target_for(record) is None:
            gaps.append("missing audio")
        if not record.evidence_images:
            gaps.append("missing evidence")
        if record.artwork_path is None:
            gaps.append("missing artwork")
        if not record.transcript_text:
            gaps.append("missing transcript")
        if record.associated_video_count == 0:
            gaps.append("missing associated videos")
        lines = [
            f"Sound: {record.title or record.music_id}",
            f"Artist/source: {record.artist or 'unknown'}",
            f"Music ID: {record.music_id}",
            f"Status: {record.status}",
            f"Usage count: {record.usage_count:,}" if record.usage_count is not None else "Usage count: unknown",
            f"Canonical URL: {record.canonical_url or 'missing'}",
            f"Folder: {record.folder_path}" if record.folder_path else "Folder: missing",
            f"Local audio: {record.local_audio_path}" if record.local_audio_path else "Local audio: missing",
            f"Artwork: {record.artwork_path}" if record.artwork_path else "Artwork: missing",
            f"Tags: {', '.join(record.tags) if record.tags else 'none'}",
            f"Quality gaps: {', '.join(gaps) if gaps else 'none'}",
        ]
        return "\n".join(lines)

    def record_duplicate_decision(
        self,
        *,
        group_id: str,
        decision: str,
        keep_music_id: str = "",
        duplicate_music_ids: list[str] | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        return self.duplicate_decisions.record_decision(
            group_id=group_id,
            decision=decision,
            keep_music_id=keep_music_id,
            duplicate_music_ids=duplicate_music_ids or [],
            notes=notes,
        )

    def duplicate_review_groups(self) -> list[DuplicateReviewGroup]:
        return load_duplicate_review_groups(self.duplicate_report_path)

    def mark_inbox_imported(self, item_id: str) -> None:
        self.inbox.mark_imported(item_id)
