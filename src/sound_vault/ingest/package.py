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
import shutil
import subprocess
import sys
import unicodedata
from typing import Any, Callable

from sound_vault.vault.metadata_io import atomic_write_json

# tagger(src, dst, tags) writes a tagged copy of src at dst
Tagger = Callable[[Path, Path, dict], None]

_NOTE_PREFIXES = ("♬", "🎵", "🎶")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _strip_notes(text: str) -> str:
    text = str(text or "")
    for prefix in _NOTE_PREFIXES:
        text = text.replace(prefix, "")
    return text.strip()


def sanitize_filename_component(value: str, max_len: int = 80) -> str:
    out: list[str] = []
    for ch in value or "":
        if ch in '/\\:*?"<>|':
            continue
        # Drop control / format / surrogate / private-use / unassigned codepoints.
        # This includes Unicode non-characters like U+FFF4 that the filesystem
        # rejects with EILSEQ ("Illegal byte sequence"). Emoji (So) are kept.
        if unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn"):
            continue
        cp = ord(ch)
        if 0xFDD0 <= cp <= 0xFDEF or (cp & 0xFFFF) >= 0xFFFE:
            continue  # explicit non-characters in every plane
        out.append(ch)
    value = re.sub(r"\s+", " ", "".join(out)).strip()
    if len(value) > max_len:
        value = value[:max_len].rsplit(" ", 1)[0]
    # Safety net: the result MUST round-trip through the filesystem encoding, or
    # mkdir/open will still raise EILSEQ on an exotic byte sequence.
    fs = sys.getfilesystemencoding() or "utf-8"
    value = value.encode(fs, "ignore").decode(fs, "ignore").strip()
    return value or "untitled"


_PLATFORM_TAGS = {"tiktok": "TT", "instagram": "IG", "youtube": "YT"}


def _platform_tag(platform: str) -> str:
    return _PLATFORM_TAGS.get((platform or "").lower(), "SC")


def build_human_filename(
    title: str, artist: str, music_id: str, status: str, ext: str = "m4a", *, platform_tag: str = "TT"
) -> str:
    title_clean = sanitize_filename_component(_strip_notes(title) or "Unknown", 60)
    author_clean = sanitize_filename_component((artist or "Unknown").strip(), 40)
    base = f"{title_clean} - {author_clean} [{platform_tag}-{music_id}] [{status}]"
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
    user_notes: str = "",
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
            human = build_human_filename(title, artist, music_id, status, platform_tag=_platform_tag(platform))
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

    # Cover artwork: if the downloader captured one, copy it in as artwork.<ext>
    # (the indexer finds it via the artwork.* glob / paths.artwork).
    rel_artwork: str | None = None
    assets: list[dict[str, Any]] = []
    cover_src = info.get("cover_path")
    if cover_src:
        cover_path = Path(str(cover_src))
        if cover_path.exists():
            ext = (cover_path.suffix or ".jpg").lstrip(".") or "jpg"
            artwork_dst = folder / f"artwork.{ext}"
            try:
                shutil.copyfile(cover_path, artwork_dst)
                rel_artwork = f"{rel_folder}/artwork.{ext}"
                assets.append({"asset_type": "artwork", "path": rel_artwork, "source": "tiktok_music_page"})
            except OSError:
                rel_artwork = None

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
        "usage_count": _optional_int(info.get("usage_count")),
        "associated_video_count": _optional_int(info.get("associated_video_count")),
        "source_title": info.get("track") or info.get("title") or None,
        "source_artist": info.get("artist") or info.get("uploader") or None,
        "source_provider": info.get("source_provider") or None,
        "source_confidence": source_confidence,
        "user_notes": user_notes,
        "tags": tags,
        "status": status,
        "paths": {
            "folder": rel_folder,
            "audio": rel_audio,
            "artwork": rel_artwork,
            "page_snapshot": None,
        },
        "assets": assets,
        "evidence": {
            "download_method": info.get("_method") or "",
            "webpage_url": info.get("webpage_url") or "",
        },
        "packaged_at": now,
    }

    metadata_path = folder / "metadata.json"
    # Atomic write: the concurrent transcription worker reads this file right after
    # ingest, so a torn (half-written) read would lose the sound.
    atomic_write_json(metadata_path, metadata)

    if append_catalog:
        _locked_append(vault_root / "catalog" / "sounds.jsonl", json.dumps(metadata, ensure_ascii=False) + "\n")

    return PackagedSound(
        music_id=music_id,
        folder=folder,
        metadata_path=metadata_path,
        audio_path=final_audio,
        metadata=metadata,
    )
