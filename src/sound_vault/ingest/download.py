"""Audio download backbone for ingest.

yt-dlp is the primary engine (multi-platform: TikTok / Instagram / YouTube / ...,
ffmpeg-backed audio extraction, no auth for public content). An optional
authenticated Playwright capture is the TikTok fallback for original-sound pages
yt-dlp cannot extract. Both implement the same small protocol so the orchestrator
and tests can inject fakes.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
from typing import Any, Callable, Protocol

# extract(url, opts) mirrors yt_dlp.YoutubeDL(opts).extract_info(url, download=True)
ExtractFn = Callable[[str, dict], dict]
# runner(cmd, cwd=None) -> (returncode, stdout, stderr)
Runner = Callable[..., "tuple[int, str, str]"]


@dataclass(frozen=True)
class DownloadResult:
    ok: bool
    audio_path: Path | None
    info: dict
    method: str
    error: str | None = None


class AudioDownloader(Protocol):
    def download(
        self, url: str, *, dest_dir: Path, basename: str, source_id: str | None = None, **kwargs: Any
    ) -> DownloadResult: ...


def _clean_info(info: dict | None) -> dict:
    info = info or {}
    return {
        "id": info.get("id"),
        "title": info.get("title") or info.get("track") or "",
        "uploader": (
            info.get("uploader")
            or info.get("creator")
            or info.get("artist")
            or info.get("uploader_id")
            or ""
        ),
        "artist": info.get("artist") or info.get("creator") or "",
        "track": info.get("track") or "",
        "duration": info.get("duration"),
        "ext": info.get("ext"),
        "webpage_url": info.get("webpage_url") or info.get("original_url") or "",
        "thumbnail": info.get("thumbnail") or "",
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
    }


def _real_extract(url: str, opts: dict) -> dict:
    import yt_dlp  # imported lazily so the package imports without the ingest extra

    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True)


class YtDlpDownloader:
    """Primary downloader: extract best audio and transcode to a single m4a."""

    def __init__(self, *, extract: ExtractFn = _real_extract, audio_format: str = "m4a") -> None:
        self._extract = extract
        self._audio_format = audio_format

    def download(
        self, url: str, *, dest_dir: Path, basename: str, source_id: str | None = None, **_: Any
    ) -> DownloadResult:
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        opts: dict[str, Any] = {
            "format": "bestaudio/best",
            "outtmpl": str(dest_dir / f"{basename}.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": self._audio_format,
                    "preferredquality": "0",
                }
            ],
        }
        try:
            info = self._extract(url, opts) or {}
        except Exception as exc:  # noqa: BLE001 - surface any yt-dlp/network failure verbatim
            return DownloadResult(
                ok=False, audio_path=None, info={}, method="yt-dlp", error=f"{type(exc).__name__}: {exc}"
            )

        audio_path = dest_dir / f"{basename}.{self._audio_format}"
        if not audio_path.exists():
            matches = sorted(p for p in dest_dir.glob(f"{basename}.*") if p.is_file())
            audio_path = matches[0] if matches else None
        if audio_path is None or not audio_path.exists():
            return DownloadResult(
                ok=False, audio_path=None, info=_clean_info(info), method="yt-dlp", error="no audio file produced"
            )
        return DownloadResult(ok=True, audio_path=audio_path, info=_clean_info(info), method="yt-dlp")


class PlaywrightCaptureDownloader:
    """TikTok fallback: drive an authenticated node/Playwright capture script.

    Unavailable (and a clean no-op) unless both the node script and the auth
    storage-state file exist, so it never blocks ingest when not configured.
    """

    def __init__(
        self,
        *,
        node_script: Path,
        storage_state: Path,
        runner: Runner,
        project_cwd: Path | None = None,
        audio_format: str = "m4a",
    ) -> None:
        self.node_script = Path(node_script)
        self.storage_state = Path(storage_state)
        self._runner = runner
        self._project_cwd = Path(project_cwd) if project_cwd else None
        self._audio_format = audio_format

    def available(self) -> bool:
        return self.node_script.exists() and self.storage_state.exists()

    def download(
        self, url: str, *, dest_dir: Path, basename: str, source_id: str | None = None, **_: Any
    ) -> DownloadResult:
        if not self.available():
            return DownloadResult(
                ok=False, audio_path=None, info={}, method="playwright", error="playwright capture unavailable"
            )
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        music_id = source_id or basename
        cmd = ["node", str(self.node_script), url, str(dest_dir), music_id, str(self.storage_state)]
        try:
            returncode, _out, err = self._runner(cmd, cwd=self._project_cwd)
        except Exception as exc:  # noqa: BLE001
            return DownloadResult(
                ok=False, audio_path=None, info={}, method="playwright", error=f"{type(exc).__name__}: {exc}"
            )
        if returncode != 0:
            return DownloadResult(
                ok=False, audio_path=None, info={}, method="playwright", error=(err or "capture failed").strip()[:500]
            )

        target = dest_dir / f"{basename}.{self._audio_format}"
        if not target.exists():
            produced = (
                sorted(dest_dir.glob("*_raw.m4a"))
                or sorted(dest_dir.glob("*_raw*"))
                or [p for p in sorted(dest_dir.glob(f"*.{self._audio_format}")) if p != target]
            )
            if not produced:
                return DownloadResult(
                    ok=False, audio_path=None, info={}, method="playwright", error="no audio captured"
                )
            shutil.move(str(produced[0]), str(target))
        return DownloadResult(
            ok=True, audio_path=target, info=self._read_capture_meta(dest_dir, music_id), method="playwright"
        )

    def capture_metadata_only(self, url: str, *, dest_dir: Path, music_id: str) -> dict:
        """Scrape only the music-page metadata + cover (no audio download).

        Used by the re-enrich worker to refresh title/author/cover/usage for an
        already-packaged sound without re-fetching audio. Returns the parsed
        capture sidecar (empty-ish on failure)."""
        if not self.available():
            return {}
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        cmd = ["node", str(self.node_script), url, str(dest_dir), music_id, str(self.storage_state), "meta-only"]
        try:
            self._runner(cmd, cwd=self._project_cwd)
        except Exception:  # noqa: BLE001 - best-effort enrichment
            return {}
        return self._read_capture_meta(dest_dir, music_id)

    @staticmethod
    def _read_capture_meta(dest_dir: Path, music_id: str) -> dict:
        """Merge the optional <music_id>_meta.json the capture script writes
        (title / author / cover / usage scraped from the same page load)."""
        info: dict[str, Any] = {"id": music_id}
        meta_path = dest_dir / f"{music_id}_meta.json"
        if not meta_path.exists():
            return info
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return info
        if not isinstance(meta, dict):
            return info
        author = str(meta.get("author") or "").strip()
        cover_path = meta.get("coverPath")
        if cover_path and not Path(str(cover_path)).is_absolute():
            cover_path = str(dest_dir / str(cover_path))
        info.update(
            {
                "title": str(meta.get("title") or "").strip(),
                "uploader": author,
                "artist": author,
                "thumbnail": str(meta.get("coverUrl") or "").strip(),
                "cover_path": cover_path if cover_path and Path(str(cover_path)).exists() else "",
                "usage_count": meta.get("usageCount"),
                "associated_video_count": meta.get("videoCount"),
                "source_provider": "TikTok",
                "webpage_url": str(meta.get("pageUrl") or "").strip(),
                # Structured sound facts from the page rehydration JSON (video/photo
                # captures only; absent on /music/ pages). Informational for now.
                "sound_is_original": meta.get("original"),
                "sound_duration": meta.get("soundDuration"),
            }
        )
        return info


class CompositeDownloader:
    """Try the primary downloader, then an optional fallback (gated by should_fallback)."""

    def __init__(
        self,
        *,
        primary: AudioDownloader,
        fallback: AudioDownloader | None = None,
        should_fallback: Callable[..., bool] | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.should_fallback = should_fallback

    def capture_metadata_only(self, url: str, *, dest_dir: Path, music_id: str) -> dict:
        """Delegate a metadata-only scrape to the Playwright fallback, if present."""
        fn = getattr(self.fallback, "capture_metadata_only", None)
        if fn is None:
            return {}
        return fn(url, dest_dir=dest_dir, music_id=music_id)

    def download(
        self, url: str, *, dest_dir: Path, basename: str, source_id: str | None = None, **extra: Any
    ) -> DownloadResult:
        result = self.primary.download(url, dest_dir=dest_dir, basename=basename, source_id=source_id)
        if result.ok:
            return result
        if self.fallback is None:
            return result
        if self.should_fallback is not None and not self.should_fallback(url, result, source_id=source_id, **extra):
            return result
        fallback_result = self.fallback.download(url, dest_dir=dest_dir, basename=basename, source_id=source_id)
        if fallback_result.ok:
            return fallback_result
        return DownloadResult(
            ok=False,
            audio_path=None,
            info=result.info,
            method="yt-dlp+playwright",
            error=f"primary: {result.error}; fallback: {fallback_result.error}",
        )
