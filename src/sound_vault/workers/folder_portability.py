"""Repair vault sound-folder (and audio-file) names for cross-filesystem portability.

Folders/files created before the portability hardening can carry names that work on
the local APFS volume but break the moment the vault is copied to NFS / ext4 / SMB /
FAT — there NAME_MAX is 255 *bytes* (not chars), so an emoji/CJK/kaomoji-heavy name
overflows with ENAMETOOLONG, and a stray Unicode non-character (e.g. U+FFF4) can be
rejected outright. This repair re-derives each non-portable folder's name from its
metadata (NFC-normalized + byte-capped, keeping the ``<music_id> - `` prefix the
indexer globs on), renames the primary audio file the same way, and rewrites the
stored paths in metadata.json + the catalog.

Read-only (dry-run) by default; pass ``--apply`` (or ``dry_run=False``) to rename.
Renaming preserves the ``<music_id> - `` prefix, so the indexer still finds every
sound by its glob even before the next reindex.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import os
from pathlib import Path

from sound_vault.ingest.package import (
    build_human_filename,
    is_portable_filename,
    portable_folder_name,
)
from sound_vault.vault.metadata_io import atomic_write_json


@dataclass
class FolderRepair:
    music_id: str
    old_folder: str  # bare folder name (under sounds/)
    new_folder: str  # == old_folder when only the audio file changed
    old_audio: str | None = None
    new_audio: str | None = None

    @property
    def folder_changed(self) -> bool:
        return self.new_folder != self.old_folder

    @property
    def audio_changed(self) -> bool:
        return self.old_audio is not None and self.new_audio is not None and self.new_audio != self.old_audio


@dataclass
class RepairResult:
    dry_run: bool = True
    scanned: int = 0
    folders_renamed: int = 0
    audio_renamed: int = 0
    repairs: list[FolderRepair] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _read_metadata(folder: Path) -> dict:
    meta_path = folder / "metadata.json"
    if not meta_path.exists():
        return {}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _parse_folder_name(name: str) -> tuple[str, str, str]:
    """Best-effort (music_id, title, artist) from a ``<id> - <title> - <artist>``
    folder name, for legacy folders missing/with-unreadable metadata."""
    parts = name.split(" - ")
    if len(parts) >= 3:
        return parts[0], " - ".join(parts[1:-1]), parts[-1]
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return parts[0], "", ""


def _primary_audio(folder: Path, meta: dict) -> Path | None:
    paths = meta.get("paths") if isinstance(meta.get("paths"), dict) else {}
    audio_rel = paths.get("audio")
    if isinstance(audio_rel, str) and audio_rel:
        candidate = folder / Path(audio_rel).name
        if candidate.exists():
            return candidate
    matches = sorted(folder.glob("*.m4a"))
    return matches[0] if matches else None


def _unique_target(parent: Path, name: str, *, source: Path) -> str:
    """Avoid clobbering an unrelated existing entry (music_id prefixes are unique, so
    this is purely defensive)."""
    target = parent / name
    if not target.exists() or target == source:
        return name
    stem = name
    for i in range(2, 1000):
        candidate = f"{stem} ({i})"
        if not (parent / candidate).exists():
            return candidate
    return name


def _plan_one(folder: Path) -> FolderRepair | None:
    meta = _read_metadata(folder)
    parsed_id, parsed_title, parsed_artist = _parse_folder_name(folder.name)
    music_id = str(meta.get("tiktok_music_id") or parsed_id or "").strip()
    if not music_id:
        return None  # caller records this as skipped
    title = str(meta.get("tiktok_visible_title") or parsed_title or "")
    artist = str(meta.get("tiktok_author_or_copyright") or parsed_artist or "")
    status = str(meta.get("status") or "ingested")

    new_folder = folder.name if is_portable_filename(folder.name) else portable_folder_name(music_id, title, artist)

    audio = _primary_audio(folder, meta)
    old_audio = audio.name if audio is not None else None
    new_audio = old_audio
    if audio is not None and not is_portable_filename(audio.name):
        ext = audio.suffix.lstrip(".") or "m4a"
        platform = str(meta.get("platform") or "tiktok")
        from sound_vault.ingest.package import _platform_tag  # local: internal helper

        new_audio = build_human_filename(title, artist, music_id, status, ext=ext, platform_tag=_platform_tag(platform))

    repair = FolderRepair(
        music_id=music_id, old_folder=folder.name, new_folder=new_folder,
        old_audio=old_audio, new_audio=new_audio,
    )
    return repair if (repair.folder_changed or repair.audio_changed) else None


def _apply_one(sounds_root: Path, repair: FolderRepair) -> FolderRepair:
    """Rename audio (inside the still-old folder) then the folder, then rebase the
    folder's metadata paths. Returns the repair with its actually-applied new_folder
    (a collision suffix may differ from the planned name)."""
    folder = sounds_root / repair.old_folder

    # 1) Rename the audio file while the folder is still at its old path.
    if repair.audio_changed:
        src = folder / repair.old_audio  # type: ignore[arg-type]
        dst_name = _unique_target(folder, repair.new_audio, source=src)  # type: ignore[arg-type]
        if src.exists():
            os.replace(src, folder / dst_name)
        repair.new_audio = dst_name

    # 2) Rename the folder (collision-safe; keeps the <music_id> - prefix).
    if repair.folder_changed:
        new_name = _unique_target(sounds_root, repair.new_folder, source=folder)
        os.replace(folder, sounds_root / new_name)
        repair.new_folder = new_name
        folder = sounds_root / new_name

    # 3) Rebase the moved folder's metadata paths.
    _rebase_metadata(folder, repair)
    return repair


def _rebase_metadata(folder: Path, repair: FolderRepair) -> None:
    meta_path = folder / "metadata.json"
    meta = _read_metadata(folder)
    if not meta:
        return
    old_prefix = f"sounds/{repair.old_folder}"
    new_prefix = f"sounds/{repair.new_folder}"

    def rebase(value: str) -> str:
        out = value
        if out.startswith(old_prefix):
            out = new_prefix + out[len(old_prefix):]
        if repair.audio_changed and repair.old_audio and out.endswith("/" + repair.old_audio):
            out = out[: -len(repair.old_audio)] + repair.new_audio  # type: ignore[operator]
        return out

    paths = meta.get("paths")
    if isinstance(paths, dict):
        for key, value in list(paths.items()):
            if isinstance(value, str) and value:
                paths[key] = rebase(value)
    assets = meta.get("assets")
    if isinstance(assets, list):
        for asset in assets:
            if isinstance(asset, dict) and isinstance(asset.get("path"), str) and asset["path"]:
                asset["path"] = rebase(asset["path"])
    atomic_write_json(meta_path, meta)


def _rebase_catalog(vault_root: Path, repairs: list[FolderRepair]) -> None:
    """Rewrite catalog rows for repaired ids so their stored paths match the new
    folder/audio names (the folder metadata is the source of truth, but keep the
    catalog consistent)."""
    catalog_path = vault_root / "catalog" / "sounds.jsonl"
    if not catalog_path.exists():
        return
    by_id = {r.music_id: r for r in repairs}
    out_lines: list[str] = []
    changed = False
    for line in catalog_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            out_lines.append(line)
            continue
        repair = by_id.get(str(data.get("tiktok_music_id") or "")) if isinstance(data, dict) else None
        if repair is None:
            out_lines.append(line)
            continue
        old_prefix, new_prefix = f"sounds/{repair.old_folder}", f"sounds/{repair.new_folder}"
        paths = data.get("paths")
        if isinstance(paths, dict):
            for key, value in list(paths.items()):
                if isinstance(value, str) and value.startswith(old_prefix):
                    rebased = new_prefix + value[len(old_prefix):]
                    if repair.audio_changed and repair.old_audio and rebased.endswith("/" + repair.old_audio):
                        rebased = rebased[: -len(repair.old_audio)] + repair.new_audio  # type: ignore[operator]
                    paths[key] = rebased
        out_lines.append(json.dumps(data, ensure_ascii=False))
        changed = True
    if changed:
        tmp = catalog_path.with_name(f".{catalog_path.name}.tmp")
        tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        os.replace(tmp, catalog_path)


def repair_folder_portability(vault_root: Path, *, dry_run: bool = True) -> RepairResult:
    vault_root = Path(vault_root)
    result = RepairResult(dry_run=dry_run)
    sounds_root = vault_root / "sounds"
    if not sounds_root.exists():
        return result

    plans: list[FolderRepair] = []
    for folder in sorted(p for p in sounds_root.iterdir() if p.is_dir()):
        result.scanned += 1
        try:
            plan = _plan_one(folder)
        except Exception as exc:  # noqa: BLE001 - one bad folder must never abort the sweep
            result.skipped.append(f"{folder.name}: {type(exc).__name__}: {exc}")
            continue
        if plan is None:
            if not is_portable_filename(folder.name):
                result.skipped.append(f"{folder.name}: no music_id, cannot rename safely")
            continue
        plans.append(plan)

    for plan in plans:
        applied = plan if dry_run else _apply_one(sounds_root, plan)
        result.repairs.append(applied)
        if applied.folder_changed:
            result.folders_renamed += 1
        if applied.audio_changed:
            result.audio_renamed += 1

    if not dry_run and result.repairs:
        _rebase_catalog(vault_root, result.repairs)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repair vault folder/audio names for filesystem portability")
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Rename files (default: dry-run)")
    args = parser.parse_args(argv)
    result = repair_folder_portability(args.vault, dry_run=not args.apply)
    mode = "APPLIED" if not result.dry_run else "DRY-RUN"
    print(
        f"[{mode}] scanned {result.scanned} folders — "
        f"{result.folders_renamed} folder(s) + {result.audio_renamed} audio file(s) "
        f"{'renamed' if not result.dry_run else 'to rename'}"
    )
    for repair in result.repairs[:20]:
        if repair.folder_changed:
            print(f"  folder: {repair.old_folder}\n       -> {repair.new_folder}")
        if repair.audio_changed:
            print(f"  audio : {repair.old_audio}\n       -> {repair.new_audio}")
    if len(result.repairs) > 20:
        print(f"  … and {len(result.repairs) - 20} more")
    for note in result.skipped[:10]:
        print(f"  [skip] {note}")
    if result.dry_run and result.repairs:
        print("re-run with --apply to rename these")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
