"""Migrate stale absolute paths in the catalog + folder sidecars to vault-relative.

Historic rows store absolute paths like ``/path/to/Sound Cache/sounds/.../x.m4a``
from when the vault lived at ``/nas``. Those break the moment the vault moves; the
indexer survives via glob fallback, but rewriting them to relative paths
(``sounds/.../x.m4a``) makes the vault portable and lets explicit-path resolution work.

Dry-run by default. Run with ``--apply`` to write changes.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import os
from pathlib import Path

_MARKERS = ("/sounds/", "/catalog/", "/reports/", "/archive/", "/inbox/")


def to_relative_vault_path(value: str) -> str:
    """Convert an absolute in-vault path to one relative to the vault root.

    Leaves already-relative paths and non-path strings (URLs, etc.) unchanged.
    """
    text = str(value or "")
    if not text.startswith("/"):
        return text
    for marker in _MARKERS:
        index = text.find(marker)
        if index != -1:
            return text[index + 1 :]  # drop the leading slash -> "sounds/..."
    return text


@dataclass
class MigrationResult:
    rows_total: int = 0
    rows_changed: int = 0
    paths_changed: int = 0
    dry_run: bool = True
    samples: list[tuple[str, str]] = field(default_factory=list)


def _migrate_paths_dict(paths: dict, result: MigrationResult) -> bool:
    changed = False
    for key, value in list(paths.items()):
        if isinstance(value, str) and value:
            new_value = to_relative_vault_path(value)
            if new_value != value:
                paths[key] = new_value
                result.paths_changed += 1
                if len(result.samples) < 8:
                    result.samples.append((value, new_value))
                changed = True
    return changed


def _migrate_assets(assets: list, result: MigrationResult) -> bool:
    changed = False
    for asset in assets:
        if isinstance(asset, dict) and isinstance(asset.get("path"), str) and asset["path"]:
            new_value = to_relative_vault_path(asset["path"])
            if new_value != asset["path"]:
                asset["path"] = new_value
                result.paths_changed += 1
                changed = True
    return changed


def _migrate_row(data: dict, result: MigrationResult) -> bool:
    changed = False
    if isinstance(data.get("paths"), dict):
        changed |= _migrate_paths_dict(data["paths"], result)
    if isinstance(data.get("assets"), list):
        changed |= _migrate_assets(data["assets"], result)
    return changed


def _rewrite_folder_metadata(vault_root: Path, result: MigrationResult, *, dry_run: bool) -> None:
    sounds_root = vault_root / "sounds"
    if not sounds_root.exists():
        return
    for metadata_path in sounds_root.glob("*/metadata.json"):
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if _migrate_row(data, result) and not dry_run:
            metadata_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def migrate_vault_paths(vault_root: Path, *, dry_run: bool = True) -> MigrationResult:
    vault_root = Path(vault_root)
    result = MigrationResult(dry_run=dry_run)
    catalog_path = vault_root / "catalog" / "sounds.jsonl"

    if catalog_path.exists():
        out_lines: list[str] = []
        for line in catalog_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                out_lines.append(line)
                continue
            if not isinstance(data, dict):
                out_lines.append(line)
                continue
            result.rows_total += 1
            if _migrate_row(data, result):
                result.rows_changed += 1
                out_lines.append(json.dumps(data, ensure_ascii=False))
            else:
                out_lines.append(line)  # preserve unchanged rows verbatim
        if not dry_run and result.rows_changed:
            tmp = catalog_path.with_name(f".{catalog_path.name}.tmp")
            tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
            os.replace(tmp, catalog_path)

    _rewrite_folder_metadata(vault_root, result, dry_run=dry_run)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate vault paths to relative (portable) form")
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    args = parser.parse_args(argv)
    result = migrate_vault_paths(args.vault, dry_run=not args.apply)
    mode = "APPLIED" if not result.dry_run else "DRY-RUN"
    print(f"[{mode}] rows: {result.rows_changed}/{result.rows_total} changed, {result.paths_changed} paths rewritten")
    for before, after in result.samples:
        print(f"  - {before}\n  + {after}")
    if result.dry_run and result.paths_changed:
        print("re-run with --apply to write these changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
