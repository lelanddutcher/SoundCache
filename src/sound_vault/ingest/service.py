"""Ingest orchestration: resolve -> download -> package -> catalog (-> index).

This is the unit that finally closes the loop from a shared URL to a packaged,
playable sound in the vault. It is headless and dependency-injected so it can be
driven from the GUI, a CLI worker, or tests.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Callable

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
        transcriber: "Callable[[Path], dict[str, Any]] | None" = None,
    ) -> None:
        self.vault_root = Path(vault_root)
        self.downloader = downloader
        self._resolve_source = resolve_source
        self._tagger = tagger
        self._index_updater = index_updater
        self._work_dir = Path(work_dir) if work_dir else self.vault_root / "inbox" / "working"
        self._now = now or _now_iso
        self._oembed_lookup = oembed_lookup
        self._transcriber = transcriber

    def _transcribe_into(self, folder: Path, audio_path: Path | None) -> bool:
        """Best-effort transcription of a packaged sound; returns True if metadata changed."""
        if self._transcriber is None or audio_path is None:
            return False
        try:
            from sound_vault.workers.transcription import transcribe_sound_folder

            result = transcribe_sound_folder(folder, audio_path=audio_path, transcriber=self._transcriber)
            return result.get("status") == "ok"
        except Exception:  # noqa: BLE001 - transcription is best-effort, never fatal to ingest
            return False

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

        # Transcribe the freshly-packaged audio (best-effort) so the index row
        # carries the transcript too.
        if self._transcribe_into(packaged.folder, packaged.audio_path):
            try:
                packaged = replace(packaged, metadata=json.loads(packaged.metadata_path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass

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

    def reenrich_existing(
        self,
        *,
        folder: Path,
        music_id: str,
        canonical_url: str,
        fetch_meta: "Callable[[str], dict]",
    ) -> dict:
        """Refresh metadata for an already-packaged sound IN PLACE.

        ``fetch_meta(canonical_url)`` returns enriched fields (title / author /
        usage_count / cover_path / source_provider). Only fills gaps — never
        overwrites a non-empty value — and never re-downloads or touches the
        existing audio. Updates metadata.json + cover artwork and re-indexes.
        Returns a small summary dict ({status, filled:[...]}).
        """
        folder = Path(folder)
        meta_path = folder / "metadata.json"
        if not meta_path.exists():
            return {"status": "skipped", "music_id": music_id, "reason": "no metadata.json"}
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"status": "failed", "music_id": music_id, "reason": f"unreadable metadata: {exc}"}

        enriched = fetch_meta(canonical_url) or {}
        filled: list[str] = []

        author = str(enriched.get("author") or enriched.get("author_name") or "").strip()
        if author and str(metadata.get("tiktok_author_or_copyright") or "").strip() in ("", "Unknown"):
            metadata["tiktok_author_or_copyright"] = author
            metadata["source_artist"] = author
            filled.append("artist")

        title = str(enriched.get("title") or "").strip()
        if title and str(metadata.get("tiktok_visible_title") or "").strip() in ("", "Unknown"):
            metadata["tiktok_visible_title"] = title
            filled.append("title")

        usage = enriched.get("usage_count")
        if usage is not None and metadata.get("usage_count") is None:
            try:
                metadata["usage_count"] = int(usage)
                filled.append("popularity")
            except (TypeError, ValueError):
                pass

        provider = str(enriched.get("source_provider") or "").strip()
        if provider and not metadata.get("source_provider"):
            metadata["source_provider"] = provider

        paths = metadata.setdefault("paths", {})
        cover = enriched.get("cover_path")
        if cover and not paths.get("artwork"):
            cover_path = Path(str(cover))
            if cover_path.exists():
                ext = (cover_path.suffix or ".jpg").lstrip(".") or "jpg"
                dst = folder / f"artwork.{ext}"
                try:
                    shutil.copyfile(cover_path, dst)
                    rel = f"{paths.get('folder') or ('sounds/' + folder.name)}/artwork.{ext}"
                    paths["artwork"] = rel
                    assets = metadata.setdefault("assets", [])
                    if isinstance(assets, list):
                        assets.append({"asset_type": "artwork", "path": rel, "source": "tiktok_music_page"})
                    filled.append("artwork")
                except OSError:
                    pass

        # Persist the metadata-gap fills first (transcription re-reads this file).
        if filled:
            metadata["packaged_at"] = self._now()
            meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

        # Transcript is its own gap: transcribe existing audio in place if missing.
        audio = self._existing_audio(folder)
        if self._transcribe_into(folder, audio):
            filled.append("transcript")

        if not filled:
            return {"status": "unchanged", "music_id": music_id, "filled": []}

        # Re-read so the index row reflects both the gap fills and any transcript.
        try:
            final_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            final_meta = metadata
        if self._index_updater is not None:
            try:
                self._index_updater(
                    PackagedSound(music_id=music_id, folder=folder, metadata_path=meta_path, audio_path=audio, metadata=final_meta)
                )
            except Exception:  # noqa: BLE001 - re-index failure shouldn't lose the metadata write
                pass
        return {"status": "enriched", "music_id": music_id, "filled": filled}

    @staticmethod
    def _existing_audio(folder: Path) -> Path | None:
        for pattern in ("*.m4a", "*.mp3", "*.aac", "*.wav", "*.ogg"):
            matches = sorted(p for p in folder.glob(pattern) if p.is_file())
            if matches:
                return matches[0]
        return None

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
