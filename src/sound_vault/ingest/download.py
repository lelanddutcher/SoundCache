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
import subprocess
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


def _has_decodable_audio(path: Path) -> bool | None:
    """Whether ffprobe sees a real audio stream. Returns None when ffprobe itself can't
    run (missing/timeout) so callers stay lenient. Guards against yt-dlp writing an
    empty/HTML/partial file that 'exists' but isn't audio (e.g. when the TikTok
    extractor is broken) -- which would otherwise be reported as a successful download
    and only blow up later in the packager with an opaque 'moov atom not found'."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:  # noqa: BLE001 - ffprobe missing/errored: don't block the download on it
        return None
    if out.returncode != 0:
        return False
    return "audio" in (out.stdout or "")


class YtDlpDownloader:
    """Primary downloader: extract best audio and transcode to a single m4a."""

    def __init__(
        self,
        *,
        extract: ExtractFn = _real_extract,
        audio_format: str = "m4a",
        probe_audio: Callable[[Path], bool | None] = _has_decodable_audio,
    ) -> None:
        self._extract = extract
        self._audio_format = audio_format
        self._probe_audio = probe_audio

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
        # A file exists -- but yt-dlp can write an empty/partial/HTML file when an
        # extractor is broken (TikTok "No working app info"). Treat a CONFIRMED
        # non-audio file as a failed download so the authenticated fallback engages,
        # rather than passing junk to the packager (which dies with a cryptic exit 183).
        if audio_path.stat().st_size == 0 or self._probe_audio(audio_path) is False:
            return DownloadResult(
                ok=False, audio_path=None, info=_clean_info(info), method="yt-dlp",
                error="yt-dlp produced an unplayable file (extractor may be broken)",
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

    def _invoke_runner(self, cmd: list[str], should_stop: Any = None) -> "tuple[int, str, str]":
        """Call the injected runner, passing a ``cancel`` predicate when it accepts
        one. Test/legacy runners with a two-arg signature still work (fall back)."""
        if should_stop is not None:
            try:
                return self._runner(cmd, cwd=self._project_cwd, cancel=should_stop)
            except TypeError:
                pass
        return self._runner(cmd, cwd=self._project_cwd)

    def download(
        self, url: str, *, dest_dir: Path, basename: str, source_id: str | None = None, **extra: Any
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
            returncode, _out, err = self._invoke_runner(cmd, extra.get("should_stop"))
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
            self._invoke_runner(cmd)
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
                # "Add to Spotify" link for published tracks, if TikTok showed one.
                "spotify_url": str(meta.get("spotifyUrl") or "").strip(),
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
        # Forward the cancel predicate only when set, so downloaders/fakes that
        # don't declare **kwargs keep their exact prior call shape.
        fwd = {"should_stop": extra["should_stop"]} if extra.get("should_stop") is not None else {}
        result = self.primary.download(url, dest_dir=dest_dir, basename=basename, source_id=source_id, **fwd)
        if result.ok:
            return result
        if self.fallback is None:
            return result
        if self.should_fallback is not None and not self.should_fallback(url, result, source_id=source_id, **extra):
            return result
        fallback_result = self.fallback.download(
            url, dest_dir=dest_dir, basename=basename, source_id=source_id, **fwd
        )
        if fallback_result.ok:
            return fallback_result
        return DownloadResult(
            ok=False,
            audio_path=None,
            info=result.info,
            method="yt-dlp+playwright",
            error=f"primary: {result.error}; fallback: {fallback_result.error}",
        )
