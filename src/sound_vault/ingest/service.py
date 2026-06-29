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
import tempfile
from typing import Any, Callable

from sound_vault.ingest.download import AudioDownloader
from sound_vault.ingest.package import PackagedSound, Tagger, ffmpeg_embed_tags, package_sound
from sound_vault.ingest.resolve import ResolvedSource, resolve
from sound_vault.ingest.shortcut_inbox import ShortcutInboxItem, ShortcutInboxStore
from sound_vault.vault.metadata_io import atomic_write_json

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
        """Best-effort transcription of a packaged sound; returns True if metadata changed.

        Logs every outcome (skip/ok/empty/error) to the diagnostics event log so a
        silent "no transcript" in the GUI is diagnosable instead of invisible."""
        from sound_vault.diagnostics import exception_fields, write_event

        if self._transcriber is None:
            write_event("ingest.transcribe_skip", folder=folder.name, reason="no_transcriber")
            return False
        if audio_path is None or not Path(audio_path).exists():
            write_event(
                "ingest.transcribe_skip", folder=folder.name,
                reason="no_audio", audio=str(audio_path),
            )
            return False
        try:
            from sound_vault.workers.transcription import transcribe_sound_folder

            result = transcribe_sound_folder(folder, audio_path=audio_path, transcriber=self._transcriber)
            write_event(
                "ingest.transcribe", folder=folder.name,
                status=str(result.get("status")), reason=str(result.get("reason") or ""),
            )
            return result.get("status") == "ok"
        except Exception as exc:  # noqa: BLE001 - transcription is best-effort, never fatal to ingest
            write_event("ingest.transcribe_error", folder=folder.name, **exception_fields(exc))
            return False

    def _download_thumbnail(self, url: str, dest_dir: Path, music_id: str) -> Path | None:
        """Fetch a yt-dlp thumbnail URL to a local cover file (best-effort, certifi SSL).

        Gives Instagram / YouTube / etc. cover artwork without a platform-specific
        scraper. Returns the saved path, or None on any failure."""
        if not url.startswith(("http://", "https://")):
            return None
        try:
            import urllib.request

            from sound_vault.net import ssl_context

            ext = "jpg"
            for cand in (".jpg", ".jpeg", ".png", ".webp"):
                if cand in url.split("?", 1)[0].lower():
                    ext = cand.lstrip(".")
                    break
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=20, context=ssl_context()) as response:  # nosec B310
                data = response.read()
            if len(data) < 256:
                return None
            dest = Path(dest_dir) / f"{music_id}_cover.{ext}"
            dest.write_bytes(data)
            return dest
        except Exception:  # noqa: BLE001 - artwork is best-effort, never fatal
            return None

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
    def _is_placeholder_slug(slug_title: str) -> bool:
        """True when the slug-derived title is a synthesized placeholder (from a
        URL we built like /music/sound-<id> for packs / favorites / deep links),
        not a real sound name — so we don't let "sound" become the title."""
        s = slug_title.strip().lower()
        return s in ("", "sound") or s.startswith("sound ")

    @staticmethod
    def _title_artist(resolved: ResolvedSource, info: dict) -> tuple[str, str]:
        download_title = str(info.get("title") or "").strip()
        artist = str(info.get("uploader") or info.get("artist") or "").strip()
        slug_title = (resolved.title_guess or "").strip()
        if IngestService._is_placeholder_slug(slug_title):
            slug_title = ""  # placeholder — defer to the real captured/oEmbed title
        if resolved.platform == "tiktok" and resolved.kind == "music":
            # A real slug (from a direct /music/<name>-<id> share) is a good title;
            # otherwise use what the capture/oEmbed actually scraped.
            title = slug_title or download_title or "Unknown"
        else:
            title = download_title or slug_title or "Unknown"
        return title, artist

    def ingest_url(
        self, url: str, *, source: str = "ios_shortcut", note: str = "", should_stop: "Callable[[], bool] | None" = None
    ) -> IngestOutcome:
        note = (note or "").strip()
        resolved = self._resolve_source(url)
        if resolved.status != "ok":
            return IngestOutcome(status="failed", url=url, reason=resolved.error or "could not resolve URL")

        music_id = resolved.music_id or resolved.source_id or _hash_id(resolved.canonical_url or url)
        if self._already_ingested(music_id):
            return IngestOutcome(
                status="duplicate", url=url, music_id=music_id, folder=self._folder_for(music_id)
            )

        # Unique per attempt so a retry (or a concurrent attempt of the same id)
        # can't reuse a half-written download dir; the finally below removes it.
        self._work_dir.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=f"{music_id}_", dir=self._work_dir))
        # For a TikTok sound (a direct /music/ share OR a video/photo we resolved to
        # its sound) capture from the canonical /music/ page — that yields the clean,
        # longest-available sound rather than the trimmed clip in the post.
        if resolved.platform == "tiktok" and resolved.kind == "music" and resolved.canonical_url:
            capture_target = resolved.canonical_url
        else:
            capture_target = resolved.final_url or url
        try:
            download = self.downloader.download(
                capture_target,
                dest_dir=work,
                basename=music_id,
                source_id=music_id,
                platform=resolved.platform,
                kind=resolved.kind,
                should_stop=should_stop,
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
            # Platform-agnostic enrichment: for any platform (Instagram, YouTube,
            # ...) fill artwork + popularity + provider from yt-dlp's own metadata
            # when the TikTok page-scrape didn't supply them.
            if not info.get("source_provider") and resolved.platform not in ("", "unknown"):
                info["source_provider"] = resolved.platform
            if info.get("usage_count") is None:
                count = info.get("view_count") or info.get("like_count")
                if count is not None:
                    info["usage_count"] = count
            if not info.get("cover_path") and info.get("thumbnail"):
                thumb = self._download_thumbnail(str(info["thumbnail"]), work, music_id)
                if thumb is not None:
                    info["cover_path"] = str(thumb)
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
                user_notes=note,
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
        # "sound" is the placeholder slug from synthesized /music/sound-<id> URLs —
        # treat it as a gap so already-imported favorites get their real title.
        if title and str(metadata.get("tiktok_visible_title") or "").strip().lower() in ("", "unknown", "sound"):
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
            atomic_write_json(meta_path, metadata)

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
        self,
        store: ShortcutInboxStore,
        *,
        max_attempts: int = 3,
        should_stop: "Callable[[], bool] | None" = None,
        on_item: "Callable[[IngestOutcome], None] | None" = None,
    ) -> list[tuple[ShortcutInboxItem, IngestOutcome]]:
        """Ingest every pending item. ``should_stop`` (e.g. the import worker's
        ``QThread.isInterruptionRequested``) is polled between items and forwarded
        into the active download so a quit during a long bulk import stops promptly
        and the interrupted item is left pending (no failure attempt burned).

        ``on_item(outcome)`` fires after each *processed* item (not the interrupted-
        mid-download case) so the caller can drive a live progress bar AND pipeline
        downstream work — e.g. queue a just-downloaded sound for transcription the
        moment it lands, instead of waiting for the whole batch to finish."""
        from sound_vault.diagnostics import exception_fields, write_event

        outcomes: list[tuple[ShortcutInboxItem, IngestOutcome]] = []
        for item in store.pending():
            if should_stop is not None and should_stop():
                break
            try:
                outcome = self.ingest_url(
                    item.url, source=item.source, note=getattr(item, "note", "") or "", should_stop=should_stop
                )
            except Exception as exc:  # noqa: BLE001 - one bad item must never abort the batch
                write_event("ingest.url_exception", url=item.url, **exception_fields(exc))
                outcome = IngestOutcome(status="failed", url=item.url, reason=f"{type(exc).__name__}: {exc}")
            # If a quit cancelled this item mid-download, leave it untouched (still
            # pending) so it retries cleanly next run rather than counting a failure.
            if should_stop is not None and should_stop() and outcome.status not in ("ingested", "duplicate"):
                break
            if outcome.status in ("ingested", "duplicate"):
                store.mark_imported(item.id)
            else:
                store.record_failure(item.id, outcome.reason or "ingest failed", max_attempts=max_attempts)
            outcomes.append((item, outcome))
            if on_item is not None:
                on_item(outcome)
        return outcomes
