"""Ingest orchestration: resolve -> download -> package -> catalog (-> index).

This is the unit that finally closes the loop from a shared URL to a packaged,
playable sound in the vault. It is headless and dependency-injected so it can be
driven from the GUI, a CLI worker, or tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import shutil
from typing import Callable

from sound_vault.ingest.download import AudioDownloader
from sound_vault.ingest.package import PackagedSound, Tagger, ffmpeg_embed_tags, package_sound
from sound_vault.ingest.resolve import ResolvedSource, resolve
from sound_vault.ingest.shortcut_inbox import ShortcutInboxItem, ShortcutInboxStore

ResolveSource = Callable[[str], ResolvedSource]
IndexUpdater = Callable[[PackagedSound], None]


@dataclass(frozen=True)
class IngestOutcome:
    status: str  # ingested | duplicate | failed
    url: str
    music_id: str | None = None
    folder: Path | None = None
    audio_path: Path | None = None
    method: str = ""
    reason: str | None = None


def _hash_id(value: str) -> str:
    return "src_" + hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:16]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class IngestService:
    def __init__(
        self,
        *,
        vault_root: Path,
        downloader: AudioDownloader,
        resolve_source: ResolveSource = resolve,
        tagger: Tagger = ffmpeg_embed_tags,
        index_updater: IndexUpdater | None = None,
        work_dir: Path | None = None,
        now: Callable[[], str] | None = None,
        oembed_lookup: Callable[[str], dict] | None = None,
    ) -> None:
        self.vault_root = Path(vault_root)
        self.downloader = downloader
        self._resolve_source = resolve_source
        self._tagger = tagger
        self._index_updater = index_updater
        self._work_dir = Path(work_dir) if work_dir else self.vault_root / "inbox" / "working"
        self._now = now or _now_iso
        self._oembed_lookup = oembed_lookup

    def _enrich_via_oembed(self, canonical_url: str) -> dict:
        """Best-effort oEmbed lookup to fill a missing title/author."""
        if not canonical_url:
            return {}
        lookup = self._oembed_lookup
        if lookup is None:
            from sound_vault.workers.oembed import fetch_sound_metadata

            lookup = fetch_sound_metadata
        try:
            return lookup(canonical_url) or {}
        except Exception:  # noqa: BLE001 - enrichment is never fatal
            return {}

    def _folder_for(self, music_id: str) -> Path | None:
        sounds_root = self.vault_root / "sounds"
        if not sounds_root.exists():
            return None
        matches = sorted(p for p in sounds_root.glob(f"{music_id} -*") if p.is_dir())
        return matches[0] if matches else None

    def _already_ingested(self, music_id: str) -> bool:
        return self._folder_for(music_id) is not None

    @staticmethod
    def _title_artist(resolved: ResolvedSource, info: dict) -> tuple[str, str]:
        download_title = str(info.get("title") or "").strip()
        artist = str(info.get("uploader") or info.get("artist") or "").strip()
        if resolved.platform == "tiktok" and resolved.kind == "music":
            title = (resolved.title_guess or "").strip() or download_title or "Unknown"
        else:
            title = download_title or (resolved.title_guess or "").strip() or "Unknown"
        return title, artist

    def ingest_url(self, url: str, *, source: str = "ios_shortcut") -> IngestOutcome:
        resolved = self._resolve_source(url)
        if resolved.status != "ok":
            return IngestOutcome(status="failed", url=url, reason=resolved.error or "could not resolve URL")

        music_id = resolved.music_id or resolved.source_id or _hash_id(resolved.canonical_url or url)
        if self._already_ingested(music_id):
            return IngestOutcome(
                status="duplicate", url=url, music_id=music_id, folder=self._folder_for(music_id)
            )

        work = self._work_dir / music_id
        work.mkdir(parents=True, exist_ok=True)
        try:
            download = self.downloader.download(
                resolved.final_url or url,
                dest_dir=work,
                basename=music_id,
                source_id=music_id,
                platform=resolved.platform,
                kind=resolved.kind,
            )
            if not download.ok:
                return IngestOutcome(
                    status="failed", url=url, music_id=music_id, reason=download.error or "download failed"
                )

            title, artist = self._title_artist(resolved, download.info)
            info = {**download.info, "_method": download.method}
            # Fill a still-thin title/author via oEmbed (capture sidecar wins when present).
            if resolved.platform == "tiktok" and (title in ("", "Unknown") or not artist):
                extra = self._enrich_via_oembed(resolved.canonical_url or url)
                if title in ("", "Unknown") and extra.get("title"):
                    title = extra["title"]
                if not artist and extra.get("author_name"):
                    artist = extra["author_name"]
                if extra.get("provider_name"):
                    info.setdefault("source_provider", extra["provider_name"])
            packaged = package_sound(
                vault_root=self.vault_root,
                music_id=music_id,
                title=title,
                artist=artist,
                canonical_url=resolved.canonical_url or "",
                source_url=url,
                platform=resolved.platform,
                audio_path=download.audio_path,
                info=info,
                status="ingested",
                tags=[source],
                tagger=self._tagger,
                now_iso=self._now(),
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)

        if self._index_updater is not None:
            try:
                self._index_updater(packaged)
            except Exception:  # noqa: BLE001 - a cache update failure must not lose the packaged sound
                pass

        return IngestOutcome(
            status="ingested",
            url=url,
            music_id=music_id,
            folder=packaged.folder,
            audio_path=packaged.audio_path,
            method=download.method,
        )

    def drain_inbox(
        self, store: ShortcutInboxStore, *, max_attempts: int = 3
    ) -> list[tuple[ShortcutInboxItem, IngestOutcome]]:
        outcomes: list[tuple[ShortcutInboxItem, IngestOutcome]] = []
        for item in store.pending():
            outcome = self.ingest_url(item.url, source=item.source)
            if outcome.status in ("ingested", "duplicate"):
                store.mark_imported(item.id)
            else:
                store.record_failure(item.id, outcome.reason or "ingest failed", max_attempts=max_attempts)
            outcomes.append((item, outcome))
        return outcomes
