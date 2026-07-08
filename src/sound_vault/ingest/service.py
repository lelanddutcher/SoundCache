"""Ingest orchestration: resolve -> download -> package -> catalog (-> index).

This is the unit that finally closes the loop from a shared URL to a packaged,
playable sound in the vault. It is headless and dependency-injected so it can be
driven from the GUI, a CLI worker, or tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
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


@dataclass(frozen=True)
class ReconcileReport:
    """The result of reconciling the durable receipt ledger + inbox against the vault:
    did everything the relay delivered actually land, and what got re-queued to recover
    the gaps. ``details`` is a human-readable line per action for the UI."""

    received: int = 0            # relay deliveries on record (the ledger's universe)
    landed: int = 0              # verified present in the vault (audio really on disk)
    in_queue: int = 0            # still pending/failed in the inbox (in progress)
    requeued: int = 0            # re-queued to recover (stranded / phantom / never-queued)
    phantom_folders: int = 0     # vault folders that claim audio but have none on disk
    unverifiable: int = 0        # imported rows with no music_id we can't cheaply verify
    details: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.received} relay deliveries on record", f"{self.landed} verified in your vault"]
        if self.in_queue:
            parts.append(f"{self.in_queue} still queued")
        if self.requeued:
            parts.append(f"{self.requeued} re-queued to recover")
        if self.phantom_folders:
            parts.append(f"{self.phantom_folders} phantom folder(s) found")
        if self.unverifiable:
            parts.append(f"{self.unverifiable} legacy import(s) unverifiable")
        return " • ".join(parts)


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
        # Do the transient capture/transcode/probe work on LOCAL disk, never on the vault.
        # On an NFS/SMB vault mount, the download step's "_raw.m4a -> <id>.m4a" move + an
        # immediate ffprobe hits close-to-open consistency: the moved target reads back as a
        # truncated stub, so a perfectly good capture is rejected as an "unplayable file" and
        # NO TikTok sound could ever be captured into the vault. Only the finished, validated
        # audio is written to the vault (by package_sound). Overridable for tests.
        self._work_dir = Path(work_dir) if work_dir else Path(tempfile.gettempdir()) / "soundcache_ingest_working"
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
        """The vault folder for an ALREADY-COMPLETE sound, or None. A folder counts as
        complete only when the package genuinely finished: metadata.json is present AND
        either its audio file is on disk, or it is an intentional audio-less (url_only)
        record. A bare/partial folder from a failed attempt does NOT count -- otherwise
        _already_ingested would return a false "duplicate" and the retry would be
        consumed with no sound saved (silent data loss)."""
        sounds_root = self.vault_root / "sounds"
        if not sounds_root.exists():
            return None
        for p in sorted(sounds_root.glob(f"{music_id} -*")):
            meta_path = p / "metadata.json"
            if not (p.is_dir() and meta_path.is_file()):
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            rel_audio = (meta.get("paths") or {}).get("audio")
            if rel_audio is None:
                return p  # intentional url_only record (no audio expected)
            audio = self.vault_root / rel_audio
            if (audio.is_file() and audio.stat().st_size > 0) or self._has_real_audio(p):
                # Trust real audio physically in the folder even when paths.audio is stale
                # or absolute — older vaults stored /nas/... paths that stop resolving after
                # a move/mount change, but the audio file is still right there. This mirrors
                # how the indexer resolves audio via its folder glob fallback, and prevents a
                # moved vault's healthy sounds from all reading as "not ingested".
                return p
        return None

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

            try:
                packaged = self._enrich_and_package(resolved, download, music_id, url, note, source, work)
            except Exception as pkg_exc:  # noqa: BLE001 - a file that downloaded but won't
                # package (e.g. a yt-dlp file that passed the decodability probe yet ffmpeg
                # still can't tag) must not dead-end: if it came from the primary and the
                # Playwright fallback is available for TikTok, re-download + package via it.
                packaged = self._retry_package_via_fallback(
                    resolved, download, music_id, url, note, source, work, capture_target, should_stop, pkg_exc
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

    def _enrich_and_package(self, resolved, download, music_id: str, url: str, note: str, source: str, work: Path):
        """Enrich the download's metadata (oEmbed / provider / artwork), then package it
        into the vault. Raises if the audio can't be packaged (e.g. ffmpeg can't tag it)."""
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
        # Platform-agnostic enrichment from yt-dlp's own metadata when the scrape didn't supply it.
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
        return package_sound(
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

    def _retry_package_via_fallback(
        self, resolved, download, music_id, url, note, source, work, capture_target, should_stop, pkg_exc
    ):
        """A file downloaded but wouldn't package. If it came from the primary (yt-dlp) and
        the Playwright fallback is available for TikTok, re-download via the fallback and
        package that. Otherwise re-raise the packaging error so the item stays FAILED in the
        queue with the real reason (drain_inbox records it; nothing is silently dropped)."""
        from sound_vault.diagnostics import exception_fields, write_event

        fb = getattr(self.downloader, "fallback", None)
        used_primary = download.method != "playwright"
        fb_available = fb is not None and bool(getattr(fb, "available", lambda: False)())
        if not (used_primary and fb_available and resolved.platform == "tiktok"):
            raise pkg_exc
        write_event("ingest.package_failed_fallback", url=url, method=download.method, **exception_fields(pkg_exc))
        # Clear the unusable primary file so the fallback capture writes cleanly into the same dir.
        try:
            if download.audio_path:
                Path(download.audio_path).unlink(missing_ok=True)
        except OSError:
            pass
        fb_download = fb.download(
            capture_target, dest_dir=work, basename=music_id, source_id=music_id,
            platform=resolved.platform, kind=resolved.kind, should_stop=should_stop,
        )
        if not fb_download.ok:
            raise RuntimeError(f"primary file unusable ({pkg_exc}); Playwright fallback failed: {fb_download.error}")
        return self._enrich_and_package(resolved, fb_download, music_id, url, note, source, work)

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
        for pattern in ("*.m4a", "*.mp3", "*.aac", "*.wav", "*.ogg", "*.opus", "*.flac"):
            # Skip macOS AppleDouble shadows (._name.m4a): on NFS/SMB these sit beside the
            # real file and are NOT audio — returning one would break transcription/preview.
            matches = sorted(p for p in folder.glob(pattern) if p.is_file() and not p.name.startswith("._"))
            if matches:
                return matches[0]
        return None

    @staticmethod
    def _has_real_audio(folder: Path) -> bool:
        """True if the folder holds a real, non-empty audio file (ignoring ._ AppleDouble
        shadows). Used to recognize a healthy sound whose metadata audio-path is stale."""
        for pattern in ("*.m4a", "*.mp3", "*.aac", "*.wav", "*.ogg", "*.opus", "*.flac"):
            for p in folder.glob(pattern):
                if p.name.startswith("._"):
                    continue
                try:
                    if p.is_file() and p.stat().st_size > 0:
                        return True
                except OSError:
                    continue
        return False

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
        from sound_vault.ingest.receipts import ReceiptLedger

        ledger = ReceiptLedger.beside(store.path)
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
            relay_id = getattr(item, "relay_id", None)
            if outcome.status in ("ingested", "duplicate"):
                # Persist the resolved id on the queue row AND to the durable ledger so
                # reconciliation can later confirm this sound's audio really landed
                # (and re-queue it if the folder turns out empty/missing).
                store.mark_imported(item.id, music_id=outcome.music_id)
                ledger.record_imported(
                    relay_id=relay_id, url=item.url, music_id=outcome.music_id,
                    folder=(str(outcome.folder) if outcome.folder else None),
                )
            else:
                store.record_failure(item.id, outcome.reason or "ingest failed", max_attempts=max_attempts)
                # Terminal once attempts are exhausted — the ledger records the fate so a
                # failed sound is never invisible even if the queue row is later cleared.
                terminal = (item.attempts + 1) >= max_attempts
                ledger.record_failed(
                    relay_id=relay_id, url=item.url, error=outcome.reason or "ingest failed", terminal=terminal
                )
            outcomes.append((item, outcome))
            if on_item is not None:
                on_item(outcome)
        return outcomes

    @staticmethod
    def _reconstruct_music_url(music_id: str) -> str:
        """Rebuild a canonical TikTok /music/ URL from a numeric music_id so a phantom
        folder (or a delivery whose original share URL we no longer hold) can still be
        re-captured. Only numeric TikTok ids are reconstructable; anything else -> ""."""
        mid = str(music_id or "").strip()
        return f"https://www.tiktok.com/music/sound-{mid}" if mid.isdigit() else ""

    def _phantom_folders(self):
        """Yield ``(folder, music_id, recover_url)`` for every vault folder that is a
        silent-data-loss casualty: it CLAIMS audio (or is a bare mkdir-before-audio
        shell) yet has no real audio on disk. Intentional url_only records and healthy
        folders are skipped. ``recover_url`` is the metadata's source/canonical URL, or
        a reconstructed /music/ URL, or "" when nothing can be recovered."""
        sounds_root = self.vault_root / "sounds"
        if not sounds_root.exists():
            return
        for folder in sorted(sounds_root.iterdir()):
            if not folder.is_dir() or folder.name.startswith("._"):
                continue
            name_id = folder.name.split(" - ", 1)[0].strip()
            meta_path = folder / "metadata.json"
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue  # unreadable metadata: don't guess, leave it be
                rel_audio = (meta.get("paths") or {}).get("audio")
                if not rel_audio:
                    continue  # url_only: intentionally audio-less, healthy
                audio = self.vault_root / rel_audio
                if (audio.is_file() and audio.stat().st_size > 0) or self._has_real_audio(folder):
                    continue  # audio really present (path OR glob fallback) -> healthy
                music_id = str(meta.get("tiktok_music_id") or name_id)
                recover = str(meta.get("source_url") or meta.get("canonical_url") or "").strip()
                yield folder, music_id, (recover or self._reconstruct_music_url(music_id))
            else:
                # A bare folder (the mkdir-before-audio phantom, from a hard crash between
                # folder.mkdir() and the metadata.json write). Recognize it by the packaged
                # folder-name SHAPE that portable_folder_name produces ("<id> - <title> -
                # <artist>"), NOT by the id's character class: Instagram/YouTube ids are
                # alphanumeric (neither numeric nor src_-prefixed), and gating on that would
                # silently skip a genuinely-lost IG/YT sound. A stray dir with no " - " (e.g.
                # "reports") is still ignored; one that already holds audio is an in-flight
                # write. A non-TikTok id yields no reconstructable URL, so it's reported (not
                # auto-recovered) -- same as the with-metadata branch.
                if " - " not in folder.name:
                    continue
                if self._has_real_audio(folder):
                    continue
                yield folder, name_id, self._reconstruct_music_url(name_id)

    def reconcile(self, store: ShortcutInboxStore) -> ReconcileReport:
        """Answer "did everything the relay delivered land in my vault?" and re-queue
        whatever didn't. Non-destructive to the vault: it only re-queues (re-ingest is
        idempotent, so re-queuing a healthy sound is a no-op duplicate).

        Three passes:
          1. Every relay delivery on the receipt ledger -> is it in the vault? A row
             marked imported whose audio is actually missing (a phantom), or a delivery
             that never even reached the queue (a crash between receipt and add), is
             re-queued from its retained URL.
          2. Any imported inbox row NOT covered by a receipt (legacy imports) is verified
             the same way.
          3. A vault sweep finds phantom folders that predate the ledger entirely and
             recovers them from their metadata's source URL (or a reconstructed one).
        """
        from sound_vault.ingest.receipts import ReceiptLedger

        ledger = ReceiptLedger.beside(store.path)
        deliveries = ledger.deliveries()
        all_items = store.all_items()
        by_relay = {i.relay_id: i for i in all_items if i.relay_id}
        by_url = {i.url: i for i in all_items}

        tally = {"landed": 0, "in_queue": 0, "unverifiable": 0}
        requeue_ids: list[str] = []
        requeue_urls: list[dict] = []
        seen_urls: set[str] = set()
        requeued_music_ids: set[str] = set()
        handled_ids: set[str] = set()
        details: list[str] = []

        def _queue_url(url: str, source: str, relay_id: str | None, note: str, why: str) -> None:
            url = (url or "").strip()
            if url and url not in seen_urls:
                requeue_urls.append({"url": url, "source": source, "relay_id": relay_id, "note": note})
                seen_urls.add(url)
                details.append(f"re-queued ({why}): {url}")

        def _classify(item: ShortcutInboxItem) -> None:
            if item.status == "imported":
                if item.music_id and self._folder_for(item.music_id) is not None:
                    tally["landed"] += 1
                elif item.music_id:
                    # marked imported but the vault has no real audio -> phantom, recover it
                    requeue_ids.append(item.id)
                    requeued_music_ids.add(item.music_id)
                    details.append(f"re-queued (imported but audio missing): {item.url}")
                else:
                    tally["unverifiable"] += 1  # legacy row, no id to check cheaply
            else:
                tally["in_queue"] += 1  # pending/failed: already visible + retryable

        # (1) reconcile relay deliveries against the vault
        for key, ev in deliveries.items():
            item = by_relay.get(key) or by_url.get(ev.url)
            if item is None:
                _queue_url(ev.url, ev.source or "relay", ev.relay_id, ev.note, "never reached the queue")
            elif item.id not in handled_ids:
                # Re-sharing the same URL yields two deliveries (two relay_ids) but ONE inbox
                # row; without this guard the row is classified twice and the report double-
                # counts it (the vault/queue stay correct — requeue is idempotent).
                handled_ids.add(item.id)
                _classify(item)

        # (2) imported inbox rows with no receipt (legacy imports) — verify too
        for item in all_items:
            if item.id not in handled_ids and item.status == "imported":
                _classify(item)

        # (3) vault sweep for phantom folders (may predate the ledger entirely)
        phantom_folders = 0
        for folder, music_id, recover_url in self._phantom_folders():
            phantom_folders += 1
            if music_id and music_id in requeued_music_ids:
                continue  # already being recovered via its inbox row
            if recover_url:
                _queue_url(recover_url, "recovery", None, "", f"vault phantom {folder.name}")
            else:
                details.append(f"phantom folder (no recoverable URL): {folder.name}")

        # apply the recovery re-queues
        requeued = 0
        for item_id in requeue_ids:
            if store.requeue(item_id):
                requeued += 1
        if requeue_urls:
            requeued += store.add_urls_bulk(requeue_urls)

        return ReconcileReport(
            received=len(deliveries),
            landed=tally["landed"],
            in_queue=tally["in_queue"],
            requeued=requeued,
            phantom_folders=phantom_folders,
            unverifiable=tally["unverifiable"],
            details=details,
        )
