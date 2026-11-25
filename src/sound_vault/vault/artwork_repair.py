"""Find and set aside artwork that is not the sound's own cover.

An older capture script saved the first small image on each TikTok music page as the
sound's artwork. On many pages that image was a profile avatar, not the cover, so one
photo ended up on hundreds of unrelated sounds.

The test here is content, not URL: a sound's cover is specific to that sound or, for
an "original sound", to its creator. When byte-identical artwork appears on
``min_sounds`` or more sounds by ``min_authors`` or more different creators, it is not
any of their covers.

Rejecting is reversible. The image is renamed to ``rejected-shared-artwork.<ext>`` (a
name the indexer does not scan for), the old ``artwork_manifest.json`` is renamed the
same way so a later run cannot reject a good replacement, and ``metadata.json`` keeps
the original values under ``artwork_rejected``. The sound then shows the "no artwork"
tile, and "Re-enrich incomplete" can fetch its real cover.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sound_vault.vault.metadata_io import atomic_write_json, read_json_lenient

ARTWORK_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
REJECTED_STEM = "rejected-shared-artwork"
MANIFEST_NAME = "artwork_manifest.json"
ARTWORK_PATH_KEYS = ("artwork", "cover_art", "cover", "thumbnail", "music_artwork")
ARTWORK_ASSET_TYPES = {"artwork", "cover_art", "sound_artwork", "music_artwork", "artwork_fallback"}


@dataclass(frozen=True)
class SharedArtwork:
    digest: str
    folders: tuple[Path, ...]
    authors: int


def _artwork_file(folder: Path) -> Path | None:
    for candidate in sorted(folder.glob("artwork.*")):
        if candidate.suffix.lower() in ARTWORK_EXTENSIONS and candidate.is_file():
            return candidate
    return None


def _metadata(folder: Path) -> dict:
    try:
        metadata = read_json_lenient(folder / "metadata.json", default={})
    except OSError:
        return {}
    return metadata if isinstance(metadata, dict) else {}


def _author(folder: Path) -> str:
    metadata = _metadata(folder)
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("tiktok_author_or_copyright") or metadata.get("source_artist") or "").strip().lower()


def find_shared_artwork(vault_root: Path, *, min_sounds: int = 3, min_authors: int = 3) -> list[SharedArtwork]:
    """Artwork files that are byte-identical across too many unrelated sounds."""
    by_digest: dict[str, list[Path]] = defaultdict(list)
    sounds = Path(vault_root) / "sounds"
    for folder in sorted(p for p in sounds.iterdir() if p.is_dir() and not p.name.startswith(".")):
        artwork = _artwork_file(folder)
        if artwork is None:
            continue
        try:
            digest = hashlib.sha256(artwork.read_bytes()).hexdigest()
        except OSError:
            continue
        by_digest[digest].append(folder)
    shared: list[SharedArtwork] = []
    for digest, folders in by_digest.items():
        if len(folders) < min_sounds:
            continue
        authors = len({_author(f) for f in folders} - {""})
        if authors >= min_authors:
            shared.append(SharedArtwork(digest, tuple(folders), authors))
    return sorted(shared, key=lambda s: -len(s.folders))


def _points_at(raw: object, target: Path) -> bool:
    return isinstance(raw, str) and bool(raw) and Path(raw).name == target.name


def reject_artwork(folder: Path, *, reason: str, digest: str = "") -> bool:
    """Set aside this folder's artwork. Returns False when there is nothing to reject."""
    folder = Path(folder)
    artwork = _artwork_file(folder)
    if artwork is None:
        return False
    metadata_path = folder / "metadata.json"
    metadata = _metadata(folder)

    paths = metadata.get("paths") if isinstance(metadata.get("paths"), dict) else {}
    removed_paths = {k: paths.pop(k) for k in list(paths) if k in ARTWORK_PATH_KEYS and _points_at(paths[k], artwork)}
    if "artwork_manifest" in paths:
        removed_paths["artwork_manifest"] = paths.pop("artwork_manifest")
    assets = metadata.get("assets")
    removed_assets = []
    if isinstance(assets, list):
        kept = []
        for asset in assets:
            if isinstance(asset, dict) and str(asset.get("asset_type") or "") in ARTWORK_ASSET_TYPES and _points_at(asset.get("path"), artwork):
                removed_assets.append(asset)
            else:
                kept.append(asset)
        metadata["assets"] = kept

    rejected = folder / f"{REJECTED_STEM}{artwork.suffix.lower()}"
    artwork.rename(rejected)
    manifest = folder / MANIFEST_NAME
    if manifest.exists():
        manifest.rename(folder / f"{REJECTED_STEM}-manifest.json")

    metadata["artwork_rejected"] = {
        "reason": reason,
        "sha256": digest,
        "original_file": artwork.name,
        "moved_to": rejected.name,
        "removed_paths": removed_paths,
        "removed_assets": removed_assets,
        "rejected_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    capture = metadata.get("capture")
    if isinstance(capture, dict):
        capture["artwork_status"] = "missing"
    audit = metadata.get("audit")
    if isinstance(audit, dict):
        audit["missing_artwork"] = True
    if paths or "paths" in metadata:
        metadata["paths"] = paths
    atomic_write_json(metadata_path, metadata)
    return True


def restore_artwork(folder: Path) -> bool:
    """Undo :func:`reject_artwork` for one folder."""
    folder = Path(folder)
    metadata_path = folder / "metadata.json"
    metadata = _metadata(folder)
    record = metadata.get("artwork_rejected")
    if not isinstance(record, dict):
        return False
    rejected = folder / str(record.get("moved_to") or "")
    original = folder / str(record.get("original_file") or "")
    if not rejected.is_file() or original.exists():
        return False
    rejected.rename(original)
    manifest = folder / f"{REJECTED_STEM}-manifest.json"
    if manifest.exists() and not (folder / MANIFEST_NAME).exists():
        manifest.rename(folder / MANIFEST_NAME)
    paths = metadata.setdefault("paths", {})
    paths.update(record.get("removed_paths") or {})
    assets = metadata.setdefault("assets", [])
    if isinstance(assets, list):
        assets.extend(record.get("removed_assets") or [])
    del metadata["artwork_rejected"]
    atomic_write_json(metadata_path, metadata)
    return True
