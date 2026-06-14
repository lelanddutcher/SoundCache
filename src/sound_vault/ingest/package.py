"""Package a downloaded sound into the file-native vault.

Produces the same on-disk shape the organizer pipeline established — a
``sounds/<id> - <title> - <artist>/`` folder, a ``metadata.json`` sidecar, and an
appended ``catalog/sounds.jsonl`` row — but stores **vault-relative paths** so the
vault stays portable across machines/mounts. The existing indexer resolves these
via its folder/audio glob fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Callable

# tagger(src, dst, tags) writes a tagged copy of src at dst
Tagger = Callable[[Path, Path, dict], None]

_NOTE_PREFIXES = ("♬", "🎵", "🎶")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_notes(text: str) -> str:
    text = str(text or "")
    for prefix in _NOTE_PREFIXES:
        text = text.replace(prefix, "")
    return text.strip()


def sanitize_filename_component(value: str, max_len: int = 80) -> str:
    value = re.sub(r'[/:*?"<>|]', "", value or "")
    value = re.sub(r"[\x00-\x1f\x7f]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > max_len:
        value = value[:max_len].rsplit(" ", 1)[0]
    return value or "untitled"


def build_human_filename(title: str, artist: str, music_id: str, status: str, ext: str = "m4a") -> str:
    title_clean = sanitize_filename_component(_strip_notes(title) or "Unknown", 60)
    author_clean = sanitize_filename_component((artist or "Unknown").strip(), 40)
    base = f"{title_clean} - {author_clean} [TT-{music_id}] [{status}]"
    return sanitize_filename_component(base, 140) + f".{ext}"


def ffmpeg_embed_tags(src: Path, dst: Path, tags: dict) -> None:
    """Default tagger: transcode to clean AAC/m4a and embed metadata via ffmpeg."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn", "-c:a", "aac", "-b:a", "192k"]
    for key, value in tags.items():
        if value:
            cmd += ["-metadata", f"{key}={value}"]
    cmd.append(str(dst))
    subprocess.run(cmd, check=True)


@dataclass(frozen=True)
class PackagedSound:
    music_id: str
    folder: Path
    metadata_path: Path
    audio_path: Path | None
    metadata: dict


def _locked_append(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    try:
        import fcntl  # POSIX advisory lock; best-effort cross-process safety

        with lock_path.open("w") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except (ImportError, OSError):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())


def _tag_dict(title: str, artist: str, music_id: str, canonical_url: str, source_confidence: str, tags: list[str]) -> dict:
    return {
        "title": _strip_notes(title) or "Unknown",
        "artist": artist or "Unknown",
        "album": "Sound Cache",
        "album_artist": "Sound Cache",
        "comment": f"music_id={music_id} | {canonical_url} | source={source_confidence}",
        "genre": ", ".join(tags),
    }


def package_sound(
    *,
    vault_root: Path,
    music_id: str,
    title: str,
    artist: str,
    canonical_url: str = "",
    mobile_music_url: str = "",
    source_url: str = "",
    platform: str = "tiktok",
    audio_path: Path | None = None,
    info: dict | None = None,
    status: str = "ingested",
    tags: list[str] | None = None,
    ingest_source: str = "ios_shortcut_relay",
    source_confidence: str = "",
    tagger: Tagger = ffmpeg_embed_tags,
    now_iso: str | None = None,
    append_catalog: bool = True,
) -> PackagedSound:
    vault_root = Path(vault_root)
    info = info or {}
    tags = list(tags or [])
    now = now_iso or _now_iso()
    if not source_confidence:
        source_confidence = "yt_dlp" if info.get("title") else "url_only"

    title_slug = sanitize_filename_component(_strip_notes(title) or "Unknown", 40)
    author_slug = sanitize_filename_component((artist or "Unknown").strip(), 30)
    folder_name = f"{music_id} - {title_slug} - {author_slug}"
    rel_folder = f"sounds/{folder_name}"
    folder = vault_root / "sounds" / folder_name
    folder.mkdir(parents=True, exist_ok=True)

    final_audio: Path | None = None
    rel_audio: str | None = None
    if audio_path is not None:
        src = Path(audio_path)
        if src.exists():
            human = build_human_filename(title, artist, music_id, status)
            dst = folder / human
            tagger(src, dst, _tag_dict(title, artist, music_id, canonical_url, source_confidence, tags))
            if dst.exists():
                final_audio = dst
                rel_audio = f"{rel_folder}/{human}"
                try:
                    if src.resolve() != dst.resolve() and src.exists():
                        src.unlink()
                except OSError:
                    pass

    metadata: dict[str, Any] = {
        "vault_version": 1,
        "tiktok_music_id": music_id,
        "platform": platform,
        "canonical_url": canonical_url,
        "mobile_music_url": mobile_music_url,
        "source_url": source_url,
        "saved_at": now,
        "ingest_source": ingest_source,
        "tiktok_visible_title": title,
        "tiktok_author_or_copyright": artist,
        "duration": info.get("duration"),
        "usage_count": None,
        "associated_video_count": None,
        "source_title": info.get("track") or None,
        "source_artist": info.get("artist") or None,
        "source_provider": None,
        "source_confidence": source_confidence,
        "tags": tags,
        "status": status,
        "paths": {
            "folder": rel_folder,
            "audio": rel_audio,
            "page_snapshot": None,
        },
        "assets": [],
        "evidence": {
            "download_method": info.get("_method") or "",
            "webpage_url": info.get("webpage_url") or "",
        },
        "packaged_at": now,
    }

    metadata_path = folder / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    if append_catalog:
        _locked_append(vault_root / "catalog" / "sounds.jsonl", json.dumps(metadata, ensure_ascii=False) + "\n")

    return PackagedSound(
        music_id=music_id,
        folder=folder,
        metadata_path=metadata_path,
        audio_path=final_audio,
        metadata=metadata,
    )
