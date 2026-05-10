from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from typing import Any


def clean_sound_title(value: Any) -> str:
    title = str(value or "").strip()
    for prefix in ("♬", "🎵", "🎶"):
        if title.startswith(prefix):
            title = title[len(prefix) :].strip()
    return title.removeprefix("-").strip()


@dataclass(frozen=True)
class CatalogStats:
    catalog_rows: int
    unique_catalog_ids: int
    duplicate_catalog_rows: int
    malformed_rows: int
    packaged_sound_folders: int


@dataclass(frozen=True)
class AssociatedVideo:
    rank: int
    video_id: str
    author_handle: str
    video_url: str
    description: str
    video_path: Path | None = None
    screenshot_path: Path | None = None
    page_title: str = ""
    captured_at: str = ""
    download_bytes: int | None = None


@dataclass(frozen=True)
class SoundRecord:
    music_id: str
    title: str
    artist: str
    tags: tuple[str, ...]
    status: str
    raw: dict[str, Any]
    associated_video_count: int = 0
    added_at: str = ""
    packaged_at: str = ""
    folder_path: Path | None = None
    local_audio_path: Path | None = None
    artwork_path: Path | None = None
    evidence_images: tuple[Path, ...] = ()
    associated_videos: tuple[AssociatedVideo, ...] = ()
    usage_count: int | None = None
    source_provider: str = ""
    source_confidence: str = ""
    vault_version: str = ""
    canonical_url: str = ""
    source_music_url: str = ""
    music_page_title: str = ""
    video_manifest_captured_at: str = ""
    transcript_text: str = ""
    transcript_language: str = ""
    transcript_path: Path | None = None
    duration_seconds: float | None = None

    @property
    def search_text(self) -> str:
        return " ".join(
            part
            for part in [
                self.music_id,
                self.title,
                self.artist,
                self.added_at,
                self.packaged_at,
                str(self.usage_count) if self.usage_count is not None else "",
                self.source_provider,
                self.source_confidence,
                self.vault_version,
                self.canonical_url,
                self.source_music_url,
                self.music_page_title,
                self.transcript_text,
                self.transcript_language,
                f"{self.duration_seconds:.2f}" if self.duration_seconds is not None else "",
                *self.tags,
            ]
            if part
        ).lower()


def _normalize_tags(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        tag = value.strip()
        return (tag,) if tag else ()
    if isinstance(value, (list, tuple)):
        normalized = []
        for tag in value:
            if tag is None:
                continue
            tag_text = str(tag).strip()
            if tag_text:
                normalized.append(tag_text)
        return tuple(normalized)
    return ()


def _safe_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _audio_duration_seconds(audio_path: Path | None) -> float | None:
    if audio_path is None or not audio_path.exists():
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=4,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return _optional_float(result.stdout.strip())


def _duration_from_data_or_audio(data: dict[str, Any], audio_path: Path | None) -> float | None:
    return _optional_float(data.get("duration_seconds") or data.get("duration")) or _audio_duration_seconds(audio_path)


def _existing_path(value: Any) -> Path | None:
    if not value:
        return None
    try:
        path = Path(str(value))
        return path if path.exists() else None
    except (OSError, ValueError):
        return None


def _folder_from_data(vault_root: Path, music_id: str, data: dict[str, Any]) -> Path | None:
    paths = data.get("paths")
    if isinstance(paths, dict):
        folder = _existing_path(paths.get("folder"))
        if folder and folder.is_dir():
            return folder
    sounds_root = vault_root / "sounds"
    if sounds_root.exists():
        matches = sorted(p for p in sounds_root.glob(f"{music_id} -*") if p.is_dir())
        if matches:
            return matches[0]
    return None


def _audio_from_data(folder: Path | None, data: dict[str, Any]) -> Path | None:
    paths = data.get("paths")
    if isinstance(paths, dict):
        for key in ("audio", "preview", "preview_audio", "m4a", "file"):
            path = _existing_path(paths.get(key))
            if path and path.is_file():
                return path
    if folder:
        matches = sorted(folder.glob("*.m4a"))
        if matches:
            return matches[0]
    return None


def _associated_videos(folder: Path | None, data: dict[str, Any]) -> tuple[AssociatedVideo, ...]:
    manifest_path = None
    paths = data.get("paths")
    if isinstance(paths, dict):
        manifest_path = _existing_path(paths.get("associated_videos_manifest"))
    if manifest_path is None and folder:
        candidate = folder / "associated_videos_manifest.json"
        manifest_path = candidate if candidate.exists() else None
    records: list[dict[str, Any]] = []
    if manifest_path:
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("records"), list):
                records = [r for r in payload["records"] if isinstance(r, dict)]
        except (OSError, json.JSONDecodeError):
            records = []
    if not records:
        assets = data.get("assets")
        if isinstance(assets, list):
            records = [asset for asset in assets if isinstance(asset, dict) and asset.get("asset_type") == "associated_video"]
    videos = []
    for idx, record in enumerate(records, start=1):
        download = record.get("download")
        download_bytes = None
        if isinstance(download, dict):
            download_bytes = _optional_int(download.get("bytes"))
        videos.append(
            AssociatedVideo(
                rank=_safe_int(record.get("rank"), default=idx),
                video_id=str(record.get("video_id") or ""),
                author_handle=str(record.get("author_handle") or record.get("author") or ""),
                video_url=str(record.get("video_url") or record.get("source_url") or ""),
                description=str(record.get("description") or ""),
                video_path=_existing_path(record.get("downloaded_video_path") or record.get("path")),
                screenshot_path=_existing_path(record.get("screenshot_path")),
                page_title=str(record.get("page_title") or ""),
                captured_at=str(record.get("captured_at") or ""),
                download_bytes=download_bytes,
            )
        )
    return tuple(videos)


def _video_manifest_context(folder: Path | None, data: dict[str, Any]) -> dict[str, str]:
    manifest_path = None
    paths = data.get("paths")
    if isinstance(paths, dict):
        manifest_path = _existing_path(paths.get("associated_videos_manifest"))
    if manifest_path is None and folder:
        candidate = folder / "associated_videos_manifest.json"
        manifest_path = candidate if candidate.exists() else None
    if manifest_path is None:
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        "source_music_url": str(payload.get("source_music_url") or ""),
        "music_page_title": str(payload.get("music_page_title") or ""),
        "captured_at": str(payload.get("captured_at") or ""),
    }


def _transcript_context(folder: Path | None, data: dict[str, Any]) -> dict[str, Any]:
    paths = data.get("paths")
    transcript_path = None
    if isinstance(paths, dict):
        transcript_path = _existing_path(paths.get("transcript") or paths.get("speech_transcript"))
    if transcript_path is None and folder:
        for name in ("transcript.json", "sound_transcript.json", "speech_transcript.json"):
            candidate = folder / name
            if candidate.exists():
                transcript_path = candidate
                break
    payload: dict[str, Any] = {}
    if transcript_path:
        try:
            loaded = json.loads(transcript_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, json.JSONDecodeError):
            payload = {}
    inline = data.get("transcript") or data.get("speech_transcript")
    if isinstance(inline, str) and inline.strip():
        payload = {**payload, "text": inline.strip()}
    elif isinstance(inline, dict):
        payload = {**payload, **inline}
    text = str(payload.get("text") or payload.get("transcript_text") or "").strip()
    if not text and isinstance(payload.get("segments"), list):
        parts = [
            str(segment.get("text") or "").strip()
            for segment in payload["segments"]
            if isinstance(segment, dict)
        ]
        text = " ".join(part for part in parts if part)
    return {
        "text": text,
        "language": str(payload.get("language") or payload.get("detected_language") or ""),
        "path": transcript_path,
    }


def _artwork_from_data(folder: Path | None, data: dict[str, Any]) -> Path | None:
    paths = data.get("paths")
    if isinstance(paths, dict):
        for key in ("artwork", "cover_art", "cover", "thumbnail", "music_artwork"):
            path = _existing_path(paths.get(key))
            if path and path.is_file():
                return path
    assets = data.get("assets")
    if isinstance(assets, list):
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            if str(asset.get("asset_type") or "") in {"artwork", "cover_art", "sound_artwork", "music_artwork"}:
                path = _existing_path(asset.get("path"))
                if path and path.is_file():
                    return path
    if folder:
        for pattern in ("artwork.*", "cover.*", "cover-art.*", "sound-artwork.*", "music-artwork.*", "thumbnail.*"):
            for candidate in sorted(folder.glob(pattern)):
                if candidate.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} and candidate.is_file():
                    return candidate
    return None


def _evidence_images(folder: Path | None, videos: tuple[AssociatedVideo, ...]) -> tuple[Path, ...]:
    images: list[Path] = []
    if folder:
        videos_dir = folder / "videos"
        if videos_dir.exists():
            images.extend(sorted(videos_dir.glob("*-music-page.jpg")))
    images.extend(video.screenshot_path for video in videos if video.screenshot_path is not None)
    deduped: list[Path] = []
    seen = set()
    for image in images:
        if image not in seen and image.exists():
            deduped.append(image)
            seen.add(image)
    return tuple(deduped)


def _catalog_music_id(data: dict[str, Any]) -> str:
    return str(data.get("tiktok_music_id") or data.get("music_id") or data.get("id") or "")


def _folder_metadata(folder: Path | None) -> dict[str, Any]:
    if folder is None:
        return {}
    metadata_path = folder / "metadata.json"
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _merge_mutable_folder_metadata(catalog_data: dict[str, Any], folder: Path | None) -> dict[str, Any]:
    """Merge folder metadata written by workers over older catalog rows without losing catalog-only context."""
    metadata = _folder_metadata(folder)
    if not metadata:
        return catalog_data
    merged = dict(catalog_data)
    for key, value in metadata.items():
        if value not in (None, "", [], {}):
            merged[key] = value
    if isinstance(catalog_data.get("paths"), dict) or isinstance(metadata.get("paths"), dict):
        paths = {}
        if isinstance(catalog_data.get("paths"), dict):
            paths.update(catalog_data["paths"])
        if isinstance(metadata.get("paths"), dict):
            paths.update({key: value for key, value in metadata["paths"].items() if value not in (None, "")})
        merged["paths"] = paths
    return merged


def inspect_catalog_stats(vault_root: Path) -> CatalogStats:
    catalog_path = vault_root / "catalog" / "sounds.jsonl"
    catalog_rows = 0
    malformed_rows = 0
    music_ids: list[str] = []
    if catalog_path.exists():
        with catalog_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    malformed_rows += 1
                    continue
                if not isinstance(data, dict):
                    malformed_rows += 1
                    continue
                music_id = _catalog_music_id(data)
                if not music_id:
                    malformed_rows += 1
                    continue
                catalog_rows += 1
                music_ids.append(music_id)
    sounds_root = vault_root / "sounds"
    packaged_sound_folders = 0
    if sounds_root.exists():
        packaged_sound_folders = sum(1 for path in sounds_root.iterdir() if path.is_dir())
    unique_catalog_ids = len(set(music_ids))
    return CatalogStats(
        catalog_rows=catalog_rows,
        unique_catalog_ids=unique_catalog_ids,
        duplicate_catalog_rows=max(0, catalog_rows - unique_catalog_ids),
        malformed_rows=malformed_rows,
        packaged_sound_folders=packaged_sound_folders,
    )


def build_index(vault_root: Path) -> list[SoundRecord]:
    catalog_path = vault_root / "catalog" / "sounds.jsonl"
    if not catalog_path.exists():
        return []
    records: list[SoundRecord] = []
    with catalog_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            music_id = _catalog_music_id(data)
            if not music_id:
                continue
            folder = _folder_from_data(vault_root, music_id, data)
            data = _merge_mutable_folder_metadata(data, folder)
            folder = _folder_from_data(vault_root, music_id, data) or folder
            audio = _audio_from_data(folder, data)
            videos = _associated_videos(folder, data)
            manifest_context = _video_manifest_context(folder, data)
            transcript_context = _transcript_context(folder, data)
            artwork = _artwork_from_data(folder, data)
            evidence_images = _evidence_images(folder, videos)
            video_count = _safe_int(data.get("associated_video_count"), default=len(videos))
            records.append(
                SoundRecord(
                    music_id=music_id,
                    title=clean_sound_title(data.get("tiktok_visible_title") or data.get("title") or ""),
                    artist=str(
                        data.get("source_artist")
                        or data.get("artist")
                        or data.get("tiktok_author_or_copyright")
                        or ""
                    ),
                    tags=_normalize_tags(data.get("tags")),
                    status=str(data.get("status") or "unreviewed"),
                    raw=data,
                    associated_video_count=video_count,
                    added_at=str(data.get("saved_at") or data.get("added_at") or ""),
                    packaged_at=str(data.get("packaged_at") or ""),
                    folder_path=folder,
                    local_audio_path=audio,
                    artwork_path=artwork,
                    evidence_images=evidence_images,
                    associated_videos=videos,
                    usage_count=_optional_int(data.get("usage_count")),
                    source_provider=str(data.get("source_provider") or ""),
                    source_confidence=str(data.get("source_confidence") or ""),
                    vault_version=str(data.get("vault_version") or ""),
                    canonical_url=str(data.get("canonical_url") or data.get("mobile_music_url") or ""),
                    source_music_url=manifest_context.get("source_music_url", ""),
                    music_page_title=manifest_context.get("music_page_title", ""),
                    video_manifest_captured_at=manifest_context.get("captured_at", ""),
                    transcript_text=transcript_context.get("text", ""),
                    transcript_language=transcript_context.get("language", ""),
                    transcript_path=transcript_context.get("path"),
                    duration_seconds=_duration_from_data_or_audio(data, audio),
                )
            )
    return _deduplicate_records(records)


def _deduplicate_records(records: list[SoundRecord]) -> list[SoundRecord]:
    latest_by_id: dict[str, SoundRecord] = {}
    for record in records:
        previous = latest_by_id.get(record.music_id)
        if previous is None or _record_sort_key(record) >= _record_sort_key(previous):
            latest_by_id[record.music_id] = record
    return list(latest_by_id.values())


def _record_sort_key(record: SoundRecord) -> tuple[str, str, int]:
    return (
        record.packaged_at or "",
        record.added_at or "",
        1 if record.local_audio_path is not None else 0,
    )
