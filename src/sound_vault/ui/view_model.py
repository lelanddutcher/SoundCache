from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
import re
import shutil
from threading import Lock
from typing import Any, Callable

from sound_vault.diagnostics import exception_fields, write_event
from sound_vault.db.index_db import IndexDatabase
from sound_vault.importers.tiktok_archive import (
    FavoriteSoundImportResult,
    write_normalized_favorite_sounds_import,
)
from sound_vault.ingest.shortcut_inbox import ShortcutInboxItem, ShortcutInboxStore
from sound_vault.relay.client import RelayClient, RelayInboxItem, _default_get_json
from sound_vault.vault.metadata_io import atomic_write_json
from sound_vault.vault.indexer import (
    CatalogStats,
    SoundRecord,
    build_index,
    hydrate_record,
    inspect_catalog_stats,
    resolve_vault_root,
    transcript_state,
)
from sound_vault.vault.library_collections import LibraryBin, LibraryCollectionsStore
from sound_vault.vault.package_writer import (
    PackageImportResult,
    latest_import_artifact_rows,
    package_imported_sounds,
    package_summary_rows,
)
from sound_vault.workers.oembed import OEmbedEnrichmentResult, enrich_favorite_sounds_oembed
from sound_vault.workers.dedupe_review import (
    DuplicateDecisionStore,
    DuplicateReviewGroup,
    append_manual_duplicate_group,
    load_duplicate_review_groups,
)


class LibraryViewModel:
    def __init__(
        self,
        *,
        vault_root: Path,
        index_path: Path,
        inbox_path: Path | None = None,
        load_sidecars: bool = True,
        sidecar_mode: str | None = None,
    ) -> None:
        self.vault_root = resolve_vault_root(vault_root)
        self.index_path = index_path
        self.load_sidecars = load_sidecars
        self.sidecar_mode = sidecar_mode
        self.inbox_path = inbox_path or self.vault_root / "inbox" / "urls" / "shortcut-inbox.jsonl"
        self.db = IndexDatabase(index_path)
        self.inbox = ShortcutInboxStore(self.inbox_path)
        self.collections = LibraryCollectionsStore(self.vault_root)
        self.duplicate_report_path = self.vault_root / "reports" / "duplicate-candidates.json"
        self.duplicate_decisions = DuplicateDecisionStore(self.vault_root / "reports" / "duplicate-decisions.jsonl")
        self._records_by_id: dict[str, SoundRecord] = {}
        self._catalog_stats = CatalogStats(0, 0, 0, 0, 0)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sound-vault-index")
        self._lock = Lock()

    def close(self) -> None:
        """Shut down the index executor — call before discarding this view model
        (e.g. switching vaults) so its worker thread doesn't leak."""
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception:  # noqa: BLE001 - teardown best-effort
            pass

    def rebuild_index(self) -> int:
        write_event(
            "index.rebuild_start",
            vault_root=str(self.vault_root),
            index_path=str(self.index_path),
            load_sidecars=self.load_sidecars,
            sidecar_mode=self.sidecar_mode or "",
        )
        try:
            catalog_stats = inspect_catalog_stats(self.vault_root)
            write_event(
                "index.catalog_inspected",
                vault_root=str(self.vault_root),
                catalog_rows=catalog_stats.catalog_rows,
                unique_catalog_ids=catalog_stats.unique_catalog_ids,
                duplicate_catalog_rows=catalog_stats.duplicate_catalog_rows,
                malformed_rows=catalog_stats.malformed_rows,
                packaged_sound_folders=catalog_stats.packaged_sound_folders,
            )
            records = build_index(self.vault_root, load_sidecars=self.load_sidecars, sidecar_mode=self.sidecar_mode)
            write_event("index.records_built", vault_root=str(self.vault_root), records=len(records))
            with self._lock:
                self._catalog_stats = catalog_stats
                self._records_by_id = {record.music_id: record for record in records}
                self.db.rebuild(records)
            write_event("index.db_rebuilt", index_path=str(self.index_path), records=len(records))
            return len(records)
        except Exception as exc:
            write_event("index.rebuild_exception", **exception_fields(exc))
            raise

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
        # Under the lock so this read-modify-write can't tear against the indexer's
        # atomic dict rebind or the import worker's record refreshes.
        with self._lock:
            for record in records:
                cached = self._records_by_id.get(record.music_id)
                if cached is not None:
                    hydrated.append(cached)
                    continue
                self._records_by_id[record.music_id] = record
                hydrated.append(record)
        return hydrated

    def is_favorite(self, music_id: str) -> bool:
        return self.collections.is_favorite(music_id)

    def toggle_favorite(self, music_id: str) -> bool:
        return self.collections.toggle_favorite(music_id)

    def favorite_music_ids(self) -> tuple[str, ...]:
        return self.collections.favorites()

    def library_bins(self) -> tuple[LibraryBin, ...]:
        return self.collections.bins()

    def create_library_bin(self, name: str) -> LibraryBin:
        return self.collections.create_bin(name)

    def add_to_library_bin(self, bin_id: str, music_id: str) -> bool:
        return self.collections.add_to_bin(bin_id, music_id)

    def library_bin_music_ids(self, bin_id: str) -> tuple[str, ...]:
        return self.collections.bin_music_ids(bin_id)

    def preview_for(self, music_id: str) -> SoundRecord:
        with self._lock:
            record = self._records_by_id.get(music_id)
        if record is None:
            record = self.db.get(music_id)
            if record is None:
                raise KeyError(music_id)
            with self._lock:
                self._records_by_id[record.music_id] = record
        if not record.raw or record.associated_video_count > len(record.associated_videos):
            record = hydrate_record(self.vault_root, record)
            with self._lock:
                self._records_by_id[record.music_id] = record
        return record

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

    def import_favorite_sounds_export(
        self,
        input_path: Path,
        *,
        date_label: str | None = None,
    ) -> FavoriteSoundImportResult:
        result = write_normalized_favorite_sounds_import(
            input_path,
            self.vault_root / "catalog" / "imports",
            date_label=date_label,
            vault_root=self.vault_root,
        )
        write_event(
            "import.favorite_sounds_normalized",
            source_file=str(input_path),
            records=result.summary.record_count,
            unique_music_ids=result.summary.unique_music_ids,
            blank_ids=result.summary.blank_ids,
            duplicate_music_ids=result.summary.duplicate_music_ids,
            malformed_rows=result.summary.malformed_rows,
            already_in_vault=result.summary.already_in_vault,
            new_to_vault=result.summary.new_to_vault,
            ambiguous_matches=result.summary.ambiguous_matches,
            json_path=str(result.json_path),
            csv_path=str(result.csv_path),
            summary_path=str(result.summary_path),
        )
        return result

    def enrich_favorite_sounds_oembed_async(
        self,
        input_path: Path,
        *,
        date_label: str | None = None,
        delay_seconds: float = 0.6,
    ) -> Future[OEmbedEnrichmentResult]:
        return self._executor.submit(
            enrich_favorite_sounds_oembed,
            input_path,
            self.vault_root / "catalog" / "imports",
            date_label=date_label,
            delay_seconds=delay_seconds,
        )

    def package_imported_sounds_async(self, input_path: Path) -> Future[PackageImportResult]:
        return self._executor.submit(package_imported_sounds, input_path, self.vault_root)

    def sounds_needing_enrichment(self, *, limit: int = 500):
        """Indexed sounds with a TikTok URL but missing artist, artwork, or popularity."""
        out = []
        for r in self.db.search("", limit=10000):
            if not (getattr(r, "canonical_url", "") or "").strip():
                continue
            if r.folder_path is None:
                continue
            has_audio = getattr(r, "local_audio_path", None) is not None
            thin = (
                (r.artist or "").strip() in ("", "Unknown")
                or r.artwork_path is None
                or r.usage_count is None
                or ((getattr(r, "transcript_text", "") or "").strip() == "" and has_audio)
            )
            if thin:
                out.append(r)
                if len(out) >= limit:
                    break
        return out

    def reenrich_incomplete_async(self, *, music_ids=None, limit: int = 50) -> Future:
        """Re-run metadata enrichment (page scrape + oEmbed) on incomplete sounds, off-thread."""
        return self._executor.submit(self._reenrich_incomplete, music_ids, limit)

    def _reenrich_incomplete(self, music_ids, limit: int) -> dict:
        import tempfile

        from sound_vault.ingest.factory import build_ingest_service
        from sound_vault.workers.oembed import fetch_sound_metadata

        service = build_ingest_service(vault_root=self.vault_root, db=self.db)
        downloader = service.downloader
        targets = self.sounds_needing_enrichment(limit=limit)
        if music_ids:
            wanted = {str(m) for m in music_ids}
            targets = [r for r in targets if r.music_id in wanted]

        summary = {"scanned": len(targets), "enriched": 0, "unchanged": 0, "failed": 0, "details": []}
        for record in targets:
            try:
                with tempfile.TemporaryDirectory(prefix="sc-reenrich-") as td:
                    def fetch_meta(url, _td=td, _mid=record.music_id):
                        merged: dict[str, Any] = {}
                        cap = getattr(downloader, "capture_metadata_only", None)
                        if cap is not None and url:
                            info = cap(url, dest_dir=Path(_td), music_id=_mid)
                            author = info.get("artist") or info.get("uploader") or ""
                            merged = {
                                "title": info.get("title") or "",
                                "author": author,
                                "usage_count": info.get("usage_count"),
                                "cover_path": info.get("cover_path") or "",
                                "source_provider": info.get("source_provider") or "",
                            }
                        if not merged.get("author") or not merged.get("title"):
                            ext = fetch_sound_metadata(url) if url else {}
                            if not merged.get("author") and ext.get("author_name"):
                                merged["author"] = ext["author_name"]
                            if not merged.get("title") and ext.get("title"):
                                merged["title"] = ext["title"]
                            merged.setdefault("source_provider", ext.get("provider_name") or "")
                        return merged

                    result = service.reenrich_existing(
                        folder=record.folder_path,
                        music_id=record.music_id,
                        canonical_url=record.canonical_url,
                        fetch_meta=fetch_meta,
                    )
            except Exception as exc:  # noqa: BLE001 - one bad sound shouldn't stop the batch
                summary["failed"] += 1
                summary["details"].append({"music_id": record.music_id, "status": "failed", "reason": str(exc)})
                continue
            status = result.get("status")
            if status == "enriched":
                summary["enriched"] += 1
                refreshed = self.db.get(record.music_id)
                if refreshed is not None:
                    with self._lock:
                        self._records_by_id[record.music_id] = refreshed
            elif status in ("unchanged",):
                summary["unchanged"] += 1
            else:
                summary["failed"] += 1
            summary["details"].append({"music_id": record.music_id, **result})
        return summary

    def add_shortcut_url(self, url: str, *, source: str, relay_id: str | None = None) -> ShortcutInboxItem:
        return self.inbox.add_url(url, source=source, relay_id=relay_id)

    def set_user_notes(self, music_id: str, notes: str) -> bool:
        """Persist a sound's user notes to metadata.json (file-native truth) + the
        index (so notes are searchable). Returns False if the sound is unknown."""
        import json as _json

        record = self.db.get(music_id)
        if record is None:
            return False
        notes = notes or ""
        folder = record.folder_path
        if folder is not None:
            meta_path = Path(folder) / "metadata.json"
            if meta_path.exists():
                try:
                    data = _json.loads(meta_path.read_text(encoding="utf-8"))
                    data["user_notes"] = notes
                    atomic_write_json(meta_path, data)
                except (OSError, ValueError):
                    pass
        self.db.update_user_notes(music_id, notes)
        refreshed = self.db.get(music_id)
        if refreshed is not None:
            with self._lock:
                self._records_by_id[music_id] = refreshed
        return True

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
        if health["pending_transcript"]:
            # Only sounds that *can* be transcribed (have audio, none yet) are
            # actionable; instrumentals (empty) aren't a gap to chase.
            rows.append(("Not transcribed yet", health["pending_transcript"], "Run local ASR sidecar worker", "all", "pending_transcript"))
        if health["missing_associated_videos"]:
            rows.append(("Missing associated videos", health["missing_associated_videos"], "Backfill example/trend video evidence", "all", "missing_videos"))
        return rows or [("No review items", 0, "Archive is empty or fully reviewed", "all", "all")]

    def archive_health_rows(self) -> list[tuple[str, str]]:
        health = self.db.archive_health_counts()
        catalog = self.catalog_stats()
        return [
            ("Vault root", str(self.vault_root)),
            ("Index database", str(self.index_path)),
            *latest_import_artifact_rows(self.vault_root),
            ("Indexed unique sounds", f"{health['total']:,}"),
            ("Catalog rows", f"{catalog.catalog_rows:,}"),
            ("Catalog unique IDs", f"{catalog.unique_catalog_ids:,}"),
            ("Duplicate catalog rows", f"{catalog.duplicate_catalog_rows:,}"),
            ("Malformed catalog rows", f"{catalog.malformed_rows:,}"),
            ("Packaged sound folders", f"{catalog.packaged_sound_folders:,}"),
            *package_summary_rows(self.vault_root),
            ("Approved sounds", f"{health['approved']:,}"),
            ("Missing local audio", f"{health['missing_audio']:,}"),
            ("Missing evidence", f"{health['missing_evidence']:,}"),
            ("Missing artwork", f"{health['missing_artwork']:,}"),
            ("Not transcribed yet", f"{health['pending_transcript']:,}"),
            ("Instrumental (no speech)", f"{health['empty_transcript']:,}"),
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
        tstate = transcript_state(record)
        if tstate == "pending":
            gaps.append("transcript not run yet")
        elif tstate == "empty":
            gaps.append("instrumental (no speech)")
        elif tstate == "no_audio":
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

    def create_manual_duplicate_group(self, music_ids: list[str] | tuple[str, ...]) -> DuplicateReviewGroup:
        candidates = []
        seen: set[str] = set()
        for music_id in music_ids:
            clean_id = str(music_id or "").strip()
            if not clean_id or clean_id in seen:
                continue
            seen.add(clean_id)
            try:
                record = self.preview_for(clean_id)
            except KeyError:
                continue
            candidates.append(self._duplicate_candidate_from_record(record))
        group = append_manual_duplicate_group(self.duplicate_report_path, candidates)
        write_event(
            "duplicate.manual_group_created",
            group_id=group.group_id,
            candidates=len(group.candidates),
            report_path=str(self.duplicate_report_path),
        )
        return group

    def duplicate_review_groups(self) -> list[DuplicateReviewGroup]:
        reviewed_group_ids = self._reviewed_duplicate_group_ids()
        return [
            group
            for group in load_duplicate_review_groups(self.duplicate_report_path)
            if group.group_id not in reviewed_group_ids
        ]

    def duplicate_candidate_preview(self, candidate: dict[str, Any]) -> SoundRecord | None:
        music_id = str(candidate.get("music_id") or "")
        if music_id:
            try:
                return self.preview_for(music_id)
            except KeyError:
                pass
        folder = self._candidate_folder(candidate)
        audio = self._existing_candidate_path(candidate.get("local_audio_path") or candidate.get("audio_path"), candidate)
        if audio is None and folder is not None:
            audio = next(iter(sorted(folder.glob("*.m4a"))), None)
        artwork = self._existing_candidate_path(candidate.get("artwork_path") or candidate.get("thumbnail_path"), candidate)
        duration = candidate.get("duration_seconds")
        try:
            duration_seconds = float(duration) if duration not in (None, "") else None
        except (TypeError, ValueError):
            duration_seconds = None
        transcript_text = str(candidate.get("transcript_excerpt") or "").strip()
        raw = {
            "tiktok_music_id": music_id,
            "tiktok_visible_title": str(candidate.get("title") or music_id or "Duplicate candidate"),
            "source_artist": str(candidate.get("artist") or candidate.get("creator") or ""),
            "paths": {
                key: str(value)
                for key, value in {
                    "folder": folder,
                    "audio": audio,
                    "artwork": artwork,
                }.items()
                if value is not None
            },
            "duration_seconds": duration_seconds,
            "transcript": {"text": transcript_text} if transcript_text else {},
            "duplicate_candidate": candidate,
        }
        return SoundRecord(
            music_id=music_id or "duplicate-candidate",
            title=str(candidate.get("title") or music_id or "Duplicate candidate"),
            artist=str(candidate.get("artist") or candidate.get("creator") or ""),
            tags=(),
            status=str(candidate.get("status") or "duplicate review"),
            raw=raw,
            folder_path=folder,
            local_audio_path=audio,
            artwork_path=artwork,
            transcript_text=transcript_text,
            duration_seconds=duration_seconds,
        )

    def duplicate_candidate_play_target(self, candidate: dict[str, Any]) -> Path | str | None:
        audio = candidate.get("local_audio_path") or candidate.get("audio_path")
        audio_path = self._existing_candidate_path(audio, candidate)
        if audio_path is not None:
            return audio_path
        music_id = str(candidate.get("music_id") or "")
        if music_id:
            try:
                return self.play_target_for(self.preview_for(music_id))
            except KeyError:
                pass
        folder = self._candidate_folder(candidate)
        if folder is not None:
            matches = sorted(folder.glob("*.m4a"))
            if matches:
                return matches[0]
        return None

    def quarantine_duplicate_candidates(
        self,
        *,
        group_id: str,
        keep_music_id: str,
        duplicate_music_ids: list[str],
    ) -> dict[str, Any]:
        timestamp = datetime.now(UTC).replace(microsecond=0).strftime("%Y%m%dT%H%M%SZ")
        safe_group = re.sub(r"[^A-Za-z0-9._-]+", "-", group_id).strip("-") or "duplicate-group"
        quarantine_root = self.vault_root / "reports" / "duplicate-quarantine" / f"{timestamp}-{safe_group}"
        quarantine_root.mkdir(parents=True, exist_ok=True)
        group = next((item for item in self.duplicate_review_groups() if item.group_id == group_id), None)
        candidates = {str(candidate.get("music_id") or ""): candidate for candidate in (group.candidates if group else ())}
        moved: list[dict[str, str]] = []
        skipped: list[dict[str, str]] = []
        keep_folder = self._candidate_folder(candidates.get(keep_music_id, {})) if keep_music_id else None
        for music_id in duplicate_music_ids:
            candidate = candidates.get(music_id, {"music_id": music_id})
            folder = self._candidate_folder(candidate)
            if folder is None:
                skipped.append({"music_id": music_id, "reason": "folder missing"})
                continue
            if keep_folder is not None and folder == keep_folder:
                skipped.append({"music_id": music_id, "reason": "same folder as keeper"})
                continue
            target = quarantine_root / folder.name
            suffix = 1
            while target.exists():
                target = quarantine_root / f"{folder.name}-{suffix}"
                suffix += 1
            shutil.move(str(folder), str(target))
            moved.append({"music_id": music_id, "from": str(folder), "to": str(target)})
        decision = self.record_duplicate_decision(
            group_id=group_id,
            decision="quarantined_duplicates",
            keep_music_id=keep_music_id,
            duplicate_music_ids=duplicate_music_ids,
            notes=f"Moved {len(moved)} duplicate folder(s) to {quarantine_root}; skipped {len(skipped)}.",
        )
        return {"quarantine_root": str(quarantine_root), "moved": moved, "skipped": skipped, "decision": decision}

    @staticmethod
    def _duplicate_candidate_from_record(record: SoundRecord) -> dict[str, Any]:
        transcript_excerpt = " ".join(record.transcript_text.split())
        return {
            "music_id": record.music_id,
            "title": record.title,
            "artist": record.artist,
            "folder": str(record.folder_path) if record.folder_path else "",
            "local_audio_path": str(record.local_audio_path) if record.local_audio_path else "",
            "artwork_path": str(record.artwork_path) if record.artwork_path else "",
            "transcript_excerpt": transcript_excerpt[:700],
            "duration_seconds": record.duration_seconds,
            "status": record.status,
            "canonical_url": record.canonical_url,
            "source_music_url": record.source_music_url,
        }

    def _reviewed_duplicate_group_ids(self) -> set[str]:
        terminal_decisions = {"duplicates", "not_duplicates", "quarantined_duplicates"}
        reviewed: set[str] = set()
        for row in self.duplicate_decisions.read_decisions():
            group_id = str(row.get("group_id") or "")
            decision = str(row.get("decision") or "")
            if group_id and decision in terminal_decisions:
                reviewed.add(group_id)
        return reviewed

    def _existing_candidate_path(self, value: Any, candidate: dict[str, Any]) -> Path | None:
        if not value:
            return None
        try:
            path = Path(str(value))
            if path.exists():
                return path
        except (OSError, ValueError):
            return None
        folder = self._candidate_folder(candidate)
        if folder is None:
            return None
        try:
            rebased = folder / Path(str(value)).name
            return rebased if rebased.exists() else None
        except (OSError, ValueError):
            return None

    def _candidate_folder(self, candidate: dict[str, Any]) -> Path | None:
        folder = candidate.get("folder") or candidate.get("folder_path")
        if folder:
            try:
                path = Path(str(folder))
                if path.exists() and path.is_dir():
                    return path
            except (OSError, ValueError):
                pass
        music_id = str(candidate.get("music_id") or "")
        if music_id:
            try:
                record = self.preview_for(music_id)
            except KeyError:
                return None
            if record.folder_path is not None and record.folder_path.exists():
                return record.folder_path
        return None

    def mark_inbox_imported(self, item_id: str) -> None:
        self.inbox.mark_imported(item_id)

    def apply_dedupe_decision(
        self,
        *,
        group_id: str,
        keep_music_id: str,
        duplicate_music_ids: list[str],
        notes: str = "",
        dry_run: bool = False,
    ) -> Any:
        """Record a keep/duplicate verdict AND act on it: archive duplicates, drop from cache."""
        from sound_vault.workers.dedupe_actions import DedupeService

        self.record_duplicate_decision(
            group_id=group_id,
            decision="duplicates",
            keep_music_id=keep_music_id,
            duplicate_music_ids=duplicate_music_ids,
            notes=notes,
        )
        result = DedupeService(self.vault_root).apply_decision(
            keep_music_id=keep_music_id, duplicate_music_ids=duplicate_music_ids, dry_run=dry_run
        )
        if not dry_run and result.archived:
            self.db.delete_many(result.archived)
            with self._lock:
                for music_id in result.archived:
                    self._records_by_id.pop(music_id, None)
                self._catalog_stats = inspect_catalog_stats(self.vault_root)
        return result

    def import_pending(self, *, reporter: Any = None) -> list[Any]:
        """Drain pending inbox links: resolve -> download -> package -> cache upsert.

        Returns the per-item ingest outcomes. Newly ingested sounds are refreshed
        into the in-memory record map so previews work without a full rebuild. If a
        reporter is given, each successful ingest reports an anonymized save event.
        """
        from sound_vault.ingest.factory import build_ingest_service

        # ASR is decoupled from the interactive import: the GUI runs a bounded
        # background transcription pass afterwards, so don't block each import on a
        # CPU-bound whisper run here (transcriber=None skips the inline attempt).
        service = build_ingest_service(vault_root=self.vault_root, db=self.db, transcriber=None)
        results = service.drain_inbox(self.inbox)
        with self._lock:
            for _item, outcome in results:
                if outcome.status == "ingested" and outcome.music_id:
                    record = self.db.get(outcome.music_id)
                    if record is not None:
                        self._records_by_id[outcome.music_id] = record
                    if reporter is not None:
                        platform = ""
                        if record is not None and isinstance(record.raw, dict):
                            platform = str(record.raw.get("platform") or "")
                        reporter.report_save(
                            sound_id=outcome.music_id,
                            platform=platform,
                            title=record.title if record else "",
                            artist=record.artist if record else "",
                        )
            # A transient FS/mount hiccup here must not fail a completed import.
            try:
                self._catalog_stats = inspect_catalog_stats(self.vault_root)
            except OSError as exc:
                write_event("import.catalog_refresh_failed", **exception_fields(exc))
        return [outcome for _item, outcome in results]

    def poll_and_import(
        self,
        *,
        base_url: str,
        pair_code: str,
        device_id: str,
        device_secret: str,
        get_json: Callable[..., dict[str, Any]] = _default_get_json,
    ) -> list[Any]:
        """Pull new links from the relay into the inbox, then ingest them."""
        self.poll_relay_inbox(
            base_url=base_url,
            pair_code=pair_code,
            device_id=device_id,
            device_secret=device_secret,
            get_json=get_json,
        )
        return self.import_pending()

    def transcription_targets(self, music_ids: "list[str] | None" = None) -> list[tuple[str, Path, Path]]:
        """Sounds that still need transcription: audio present, no transcript yet.

        Returns ``(music_id, folder, audio_path)`` tuples. Pass ``music_ids`` to
        scope to a specific set (e.g. a freshly-imported batch); omit to scan the
        whole index. Used to drive a bounded background transcription pass."""
        with self._lock:
            if music_ids is None:
                records = list(self._records_by_id.values())
            else:
                records = [self._records_by_id.get(m) for m in music_ids]
        targets: list[tuple[str, Path, Path]] = []
        for record in records:
            if record is None or transcript_state(record) != "pending":
                continue
            folder = record.folder_path
            audio = record.local_audio_path
            if folder is not None and audio is not None:
                targets.append((record.music_id, Path(folder), Path(audio)))
        return targets

    def transcribe_targets(
        self,
        targets: "list[tuple[str, Path, Path]]",
        *,
        transcriber: "Callable[[Path], dict[str, Any]] | None" = None,
        progress: "Callable[[int, int, str], None] | None" = None,
    ) -> int:
        """Transcribe a bounded list of sounds in place (writes metadata.json).

        Idempotent (skips sounds that already have a transcript) and best-effort
        per item, so one failure never aborts the batch. Builds the local
        faster-whisper transcriber once unless one is injected. Returns how many
        sounds gained a transcript result."""
        if not targets:
            return 0
        from sound_vault.workers.transcription import transcribe_sound_folder

        if transcriber is None:
            from sound_vault.ingest.factory import build_transcriber

            transcriber = build_transcriber()
        if transcriber is None:
            return 0
        done = 0
        total = len(targets)
        for index, (music_id, folder, audio) in enumerate(targets):
            try:
                result = transcribe_sound_folder(Path(folder), audio_path=Path(audio), transcriber=transcriber)
                if result.get("status") in ("ok", "empty"):
                    done += 1
                else:
                    write_event(
                        "transcribe.skip", music_id=str(music_id),
                        status=str(result.get("status")), reason=str(result.get("reason") or ""),
                    )
            except Exception as exc:  # noqa: BLE001 - per-item best-effort, but never silent
                write_event("transcribe.error", music_id=str(music_id), **exception_fields(exc))
            if progress is not None:
                progress(index + 1, total, music_id)
        return done
