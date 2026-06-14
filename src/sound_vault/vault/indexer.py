from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Literal

from sound_vault.vault.hashtags import (
    enrich_video_record_hashtags,
    unique_hashtags,
)

SidecarMode = Literal["none", "summary", "full"]


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
    hashtags: tuple[str, ...] = ()


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
    hashtags: tuple[str, ...] = ()
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
    user_notes: str = ""

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
                self.user_notes,
                f"{self.duration_seconds:.2f}" if self.duration_seconds is not None else "",
                *self.tags,
                *self.hashtags,
                *(f"#{tag}" for tag in self.hashtags),
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


def _record_hashtags(data: dict[str, Any], videos: tuple[AssociatedVideo, ...]) -> tuple[str, ...]:
    tags: list[str] = []
    for key in ("hashtags", "associated_video_hashtags"):
        value = data.get(key)
        if isinstance(value, str):
            tags.append(value)
        elif isinstance(value, (list, tuple)):
            tags.extend(str(item) for item in value)
    for video in videos:
        tags.extend(video.hashtags)
    return unique_hashtags(tags)


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
    duration = _optional_float(data.get("duration_seconds") or data.get("duration"))
    if duration is not None:
        return duration
    # Library indexing must stay fast and safe for first launch. Older V1 builds
    # only parsed catalog metadata; probing thousands of files with ffprobe during
    # a GUI rebuild can look like a hang or trigger target-machine instability.
    # Explicitly opt in for diagnostics/backfill jobs, not normal app indexing.
    if os.getenv("SOUND_VAULT_PROBE_AUDIO_DURATIONS") == "1":
        return _audio_duration_seconds(audio_path)
    return None


def _existing_path(value: Any) -> Path | None:
    if not value:
        return None
    try:
        path = Path(str(value))
        return path if path.exists() else None
    except (OSError, ValueError):
        return None


def _existing_or_rebased_path(value: Any, folder: Path | None) -> Path | None:
    path = _existing_path(value)
    if path is not None:
        return path
    if not value or folder is None:
        return None
    try:
        name = Path(str(value)).name
    except (OSError, ValueError):
        return None
    if not name:
        return None
    candidate = folder / name
    try:
        return candidate if candidate.exists() else None
    except OSError:
        return None


def _rebased_path_no_validate(value: Any, folder: Path | None) -> Path | None:
    if not value:
        return None
    try:
        path = Path(str(value))
    except (OSError, ValueError):
        return None
    if folder is None:
        return path
    return folder / path.name


def _looks_like_sounds_root(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    try:
        for child in path.iterdir():
            if child.is_dir() and (child / "metadata.json").exists():
                return True
    except OSError:
        return False
    return False


def resolve_vault_root(path: Path) -> Path:
    """Normalize user-selected paths to the vault root."""
    selected = path.expanduser()
    if selected.name.lower() == "sounds" and _looks_like_sounds_root(selected):
        return selected.parent
    return selected


def _folder_from_data(
    vault_root: Path,
    music_id: str,
    data: dict[str, Any],
    folder_hint: Path | None = None,
    folder_lookup: dict[str, Path] | None = None,
    *,
    validate: bool = True,
) -> Path | None:
    if not validate:
        if folder_hint is not None:
            return folder_hint
        if folder_lookup and music_id in folder_lookup:
            return folder_lookup[music_id]
        paths = data.get("paths")
        if isinstance(paths, dict):
            raw = paths.get("folder")
            if raw:
                try:
                    return Path(str(raw))
                except (OSError, ValueError):
                    return None
        return None
    if folder_hint is not None and folder_hint.exists() and folder_hint.is_dir():
        return folder_hint
    paths = data.get("paths")
    if isinstance(paths, dict):
        folder = _existing_path(paths.get("folder"))
        if folder and folder.is_dir():
            return folder
    if folder_lookup and music_id in folder_lookup:
        return folder_lookup[music_id]
    sounds_root = vault_root / "sounds"
    if sounds_root.exists():
        matches = sorted(p for p in sounds_root.glob(f"{music_id} -*") if p.is_dir())
        if matches:
            return matches[0]
    return None


def _audio_from_data(
    folder: Path | None,
    data: dict[str, Any],
    *,
    scan_folder: bool = True,
    validate: bool = True,
) -> Path | None:
    paths = data.get("paths")
    if isinstance(paths, dict):
        for key in ("audio", "preview", "preview_audio", "m4a", "file"):
            raw = paths.get(key)
            if not raw:
                continue
            if validate:
                path = _existing_or_rebased_path(raw, folder)
                if path and path.is_file():
                    return path
            else:
                candidate = _rebased_path_no_validate(raw, folder)
                if candidate is not None:
                    return candidate
    if folder and scan_folder:
        matches = sorted(folder.glob("*.m4a"))
        if matches:
            return matches[0]
    return None


def _coerce_sidecar_mode(*, load_sidecars: bool, sidecar_mode: SidecarMode | None = None) -> SidecarMode:
    if sidecar_mode is not None:
        if sidecar_mode not in {"none", "summary", "full"}:
            raise ValueError(f"unknown sidecar mode: {sidecar_mode}")
        return sidecar_mode
    return "full" if load_sidecars else "none"


def _associated_videos(folder: Path | None, data: dict[str, Any], *, load_sidecars: bool = True) -> tuple[AssociatedVideo, ...]:
    manifest_path = None
    paths = data.get("paths")
    if isinstance(paths, dict):
        manifest_path = _existing_or_rebased_path(paths.get("associated_videos_manifest"), folder)
    if manifest_path is None and folder:
        candidate = folder / "associated_videos_manifest.json"
        manifest_path = candidate if candidate.exists() else None
    records: list[dict[str, Any]] = []
    if manifest_path and load_sidecars:
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
    if not records and load_sidecars and folder:
        videos_jsonl = folder / "videos.jsonl"
        if videos_jsonl.exists():
            try:
                with videos_jsonl.open("r", encoding="utf-8") as handle:
                    records = [json.loads(line) for line in handle if line.strip()]
                records = [record for record in records if isinstance(record, dict)]
            except (OSError, json.JSONDecodeError):
                records = []
    videos = []
    def video_asset_path(value: Any) -> Path | None:
        path = _existing_or_rebased_path(value, folder)
        if path is not None or not value or folder is None:
            return path
        try:
            name = Path(str(value)).name
        except (OSError, ValueError):
            return None
        if not name:
            return None
        candidate = folder / "videos" / name
        try:
            return candidate if candidate.exists() else None
        except OSError:
            return None

    for idx, record in enumerate(records, start=1):
        record = enrich_video_record_hashtags(record)
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
                video_path=video_asset_path(record.get("downloaded_video_path") or record.get("path")),
                screenshot_path=video_asset_path(record.get("screenshot_path")),
                page_title=str(record.get("page_title") or ""),
                captured_at=str(record.get("captured_at") or ""),
                download_bytes=download_bytes,
                hashtags=unique_hashtags(record.get("hashtags") or ()),
            )
        )
    return tuple(videos)


def _video_manifest_context(folder: Path | None, data: dict[str, Any], *, load_sidecars: bool = True) -> dict[str, str]:
    if not load_sidecars:
        return {}
    manifest_path = None
    paths = data.get("paths")
    if isinstance(paths, dict):
        manifest_path = _existing_or_rebased_path(paths.get("associated_videos_manifest"), folder)
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


def _transcript_context(folder: Path | None, data: dict[str, Any], *, load_sidecars: bool = True) -> dict[str, Any]:
    paths = data.get("paths")
    transcript_path = None
    if isinstance(paths, dict):
        transcript_path = _existing_or_rebased_path(
            paths.get("transcript") or paths.get("speech_transcript"),
            folder,
        )
    v2 = data.get("speech_transcript_v2")
    v2_texts: list[str] = []
    v2_language = ""
    if isinstance(v2, dict):
        v2_language = str(v2.get("language") or v2.get("detected_language") or "")
        best_text = str(v2.get("best_text") or v2.get("text") or v2.get("transcript_text") or "").strip()
        if best_text:
            v2_texts.append(best_text)
        alternates = v2.get("alternates")
        if isinstance(alternates, list):
            for alternate in alternates:
                alternate_text = str(alternate or "").strip()
                if alternate_text and alternate_text not in v2_texts:
                    v2_texts.append(alternate_text)
    inline = data.get("transcript") or data.get("speech_transcript")
    if isinstance(inline, str) and inline.strip():
        text = " ".join(v2_texts + [inline.strip()]) if v2_texts else inline.strip()
        return {"text": text, "language": v2_language, "path": transcript_path}
    if isinstance(inline, dict):
        text = str(inline.get("text") or inline.get("transcript_text") or "").strip()
        if not text and isinstance(inline.get("segments"), list):
            parts = [
                str(segment.get("text") or "").strip()
                for segment in inline["segments"]
                if isinstance(segment, dict)
            ]
            text = " ".join(part for part in parts if part)
        combined = " ".join(v2_texts + ([text] if text else [])) if v2_texts else text
        if combined:
            return {
                "text": combined,
                "language": v2_language or str(inline.get("language") or inline.get("detected_language") or ""),
                "path": transcript_path,
            }
    if v2_texts:
        return {"text": " ".join(v2_texts), "language": v2_language, "path": transcript_path}
    if not load_sidecars:
        return {"text": "", "language": "", "path": transcript_path}
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


def _artwork_from_data(
    folder: Path | None,
    data: dict[str, Any],
    *,
    scan_folder: bool = True,
    validate: bool = True,
) -> Path | None:
    paths = data.get("paths")
    if isinstance(paths, dict):
        for key in ("artwork", "cover_art", "cover", "thumbnail", "music_artwork"):
            raw = paths.get(key)
            if not raw:
                continue
            if validate:
                path = _existing_or_rebased_path(raw, folder)
                if path and path.is_file():
                    return path
            else:
                candidate = _rebased_path_no_validate(raw, folder)
                if candidate is not None:
                    return candidate
    assets = data.get("assets")
    if isinstance(assets, list):
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            if str(asset.get("asset_type") or "") in {"artwork", "cover_art", "sound_artwork", "music_artwork"}:
                raw = asset.get("path")
                if not raw:
                    continue
                if validate:
                    path = _existing_path(raw)
                    if path and path.is_file():
                        return path
                else:
                    candidate = _rebased_path_no_validate(raw, folder)
                    if candidate is not None:
                        return candidate
    if folder and scan_folder:
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
            images.extend(sorted(videos_dir.glob("*.jpg")))
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
    vault_root = resolve_vault_root(vault_root)
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
        try:
            packaged_sound_folders = sum(1 for path in sounds_root.iterdir() if path.is_dir())
        except OSError:
            packaged_sound_folders = 0
    unique_catalog_ids = len(set(music_ids))
    return CatalogStats(
        catalog_rows=catalog_rows,
        unique_catalog_ids=unique_catalog_ids,
        duplicate_catalog_rows=max(0, catalog_rows - unique_catalog_ids),
        malformed_rows=malformed_rows,
        packaged_sound_folders=packaged_sound_folders,
    )


def _iter_packaged_folder_metadata(vault_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    sounds_root = vault_root / "sounds"
    if not sounds_root.exists() and _looks_like_sounds_root(vault_root):
        sounds_root = vault_root
    if not sounds_root.exists():
        return []
    rows: list[tuple[Path, dict[str, Any]]] = []
    try:
        folders = sorted(path for path in sounds_root.iterdir() if path.is_dir())
    except OSError:
        return rows
    for folder in folders:
        data = _folder_metadata(folder)
        if data and _catalog_music_id(data):
            rows.append((folder, data))
    return rows


def _sound_folder_lookup(vault_root: Path) -> dict[str, Path]:
    sounds_root = vault_root / "sounds"
    if not sounds_root.exists() and _looks_like_sounds_root(vault_root):
        sounds_root = vault_root
    if not sounds_root.exists():
        return {}
    lookup: dict[str, Path] = {}
    try:
        folders = sorted(path for path in sounds_root.iterdir() if path.is_dir())
    except OSError:
        return lookup
    for folder in folders:
        music_id = folder.name.split(" -", 1)[0].strip()
        if music_id and music_id not in lookup:
            lookup[music_id] = folder
    return lookup


def _record_from_data(
    vault_root: Path,
    data: dict[str, Any],
    *,
    folder_hint: Path | None = None,
    folder_lookup: dict[str, Path] | None = None,
    load_sidecars: bool = True,
    sidecar_mode: SidecarMode | None = None,
) -> SoundRecord | None:
    music_id = _catalog_music_id(data)
    if not music_id:
        return None
    mode = _coerce_sidecar_mode(load_sidecars=load_sidecars, sidecar_mode=sidecar_mode)
    read_summary = mode in {"summary", "full"}
    read_full = mode == "full"
    folder = _folder_from_data(vault_root, music_id, data, folder_hint, folder_lookup, validate=read_full)
    if read_summary:
        data = _merge_mutable_folder_metadata(data, folder)
        folder = _folder_from_data(vault_root, music_id, data, folder_hint, folder_lookup, validate=read_full) or folder
    audio = _audio_from_data(folder, data, scan_folder=read_summary, validate=read_full)
    videos = _associated_videos(folder, data, load_sidecars=read_full)
    manifest_context = _video_manifest_context(folder, data, load_sidecars=read_full)
    transcript_context = _transcript_context(folder, data, load_sidecars=read_summary)
    hashtags = _record_hashtags(data, videos)
    artwork = _artwork_from_data(folder, data, scan_folder=read_summary, validate=read_full)
    evidence_images = _evidence_images(folder, videos) if read_summary else ()
    video_count = _safe_int(data.get("associated_video_count"), default=len(videos))
    return SoundRecord(
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
        hashtags=hashtags,
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
        user_notes=str(data.get("user_notes") or ""),
    )


def _data_from_record(record: SoundRecord) -> dict[str, Any]:
    data = dict(record.raw) if isinstance(record.raw, dict) else {}
    data.setdefault("tiktok_music_id", record.music_id)
    data.setdefault("tiktok_visible_title", record.title)
    data.setdefault("source_artist", record.artist)
    data.setdefault("tags", list(record.tags))
    data.setdefault("hashtags", list(record.hashtags))
    data.setdefault("status", record.status)
    data.setdefault("associated_video_count", record.associated_video_count)
    data.setdefault("saved_at", record.added_at)
    data.setdefault("packaged_at", record.packaged_at)
    data.setdefault("usage_count", record.usage_count)
    data.setdefault("source_provider", record.source_provider)
    data.setdefault("source_confidence", record.source_confidence)
    data.setdefault("vault_version", record.vault_version)
    data.setdefault("canonical_url", record.canonical_url)
    data.setdefault("user_notes", record.user_notes)
    paths = dict(data.get("paths") if isinstance(data.get("paths"), dict) else {})
    if record.folder_path is not None:
        paths.setdefault("folder", str(record.folder_path))
    if record.local_audio_path is not None:
        paths.setdefault("audio", str(record.local_audio_path))
    if record.artwork_path is not None:
        paths.setdefault("artwork", str(record.artwork_path))
    if record.transcript_path is not None:
        paths.setdefault("transcript", str(record.transcript_path))
    if paths:
        data["paths"] = paths
    return data


def hydrate_record(vault_root: Path, record: SoundRecord) -> SoundRecord:
    """Load full sidecar detail for one selected record without slowing startup indexing."""
    hydrated = _record_from_data(
        resolve_vault_root(vault_root),
        _data_from_record(record),
        folder_hint=record.folder_path,
        load_sidecars=True,
        sidecar_mode="full",
    )
    return hydrated or record


def build_record(vault_root: Path, data: dict[str, Any]) -> SoundRecord | None:
    """Build one SoundRecord from a single catalog/metadata dict.

    Public single-record path used by the ingest flow's incremental upsert. Reuses
    build_index's per-row enrichment (sidecars, hashtags) so a freshly-packaged
    sound indexes identically to a full rebuild.
    """
    return _record_from_data(resolve_vault_root(vault_root), data)


def _index_worker_count() -> int:
    raw = os.environ.get("SOUND_VAULT_INDEX_WORKERS", "").strip()
    if raw:
        try:
            n = int(raw)
            return max(1, min(64, n))
        except ValueError:
            pass
    return 16


def build_index(
    vault_root: Path,
    *,
    load_sidecars: bool = True,
    sidecar_mode: SidecarMode | None = None,
) -> list[SoundRecord]:
    vault_root = resolve_vault_root(vault_root)
    mode = _coerce_sidecar_mode(load_sidecars=load_sidecars, sidecar_mode=sidecar_mode)
    catalog_path = vault_root / "catalog" / "sounds.jsonl"
    records: list[SoundRecord] = []
    catalog_ids: set[str] = set()
    folder_lookup = _sound_folder_lookup(vault_root)

    catalog_rows: list[dict[str, Any]] = []
    if catalog_path.exists():
        with catalog_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    catalog_rows.append(data)

    def _build_from_catalog(data: dict[str, Any]) -> SoundRecord | None:
        return _record_from_data(
            vault_root,
            data,
            folder_lookup=folder_lookup,
            load_sidecars=load_sidecars,
            sidecar_mode=mode,
        )

    if catalog_rows:
        workers = _index_worker_count() if mode != "none" else 1
        if workers > 1 and len(catalog_rows) > 8:
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="sound-vault-build-index"
            ) as pool:
                built = list(pool.map(_build_from_catalog, catalog_rows))
        else:
            built = [_build_from_catalog(row) for row in catalog_rows]
        for record in built:
            if record is not None:
                catalog_ids.add(record.music_id)
                records.append(record)

    should_scan_packaged_folders = mode == "full" or not catalog_ids
    if should_scan_packaged_folders:
        packaged = [
            (folder, data)
            for folder, data in _iter_packaged_folder_metadata(vault_root)
            if _catalog_music_id(data) not in catalog_ids
        ]

        def _build_from_packaged(item: tuple[Path, dict[str, Any]]) -> SoundRecord | None:
            folder, data = item
            return _record_from_data(
                vault_root,
                data,
                folder_hint=folder,
                folder_lookup=folder_lookup,
                load_sidecars=load_sidecars,
                sidecar_mode=mode,
            )

        if packaged:
            workers = _index_worker_count() if mode != "none" else 1
            if workers > 1 and len(packaged) > 8:
                with ThreadPoolExecutor(
                    max_workers=workers, thread_name_prefix="sound-vault-build-pkg"
                ) as pool:
                    built = list(pool.map(_build_from_packaged, packaged))
            else:
                built = [_build_from_packaged(item) for item in packaged]
            for record in built:
                if record is not None:
                    records.append(record)
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
