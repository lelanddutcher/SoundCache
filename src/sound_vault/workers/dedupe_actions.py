"""Act on human duplicate-review decisions.

Where dedupe_review only *records* keep/duplicate verdicts, this executes them:
it archives the duplicate's folder out of ``sounds/`` and drops its catalog rows,
so duplicates leave the active library. Every action is reversible — the folder
is moved (not deleted) to ``archive/dedupe/`` and an undo entry is logged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _catalog_music_id(data: dict[str, Any]) -> str:
    return str(data.get("tiktok_music_id") or data.get("music_id") or data.get("id") or "")


@dataclass
class DedupeActionResult:
    keep_music_id: str
    archived: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    dry_run: bool = False
    undo_entries: list[dict] = field(default_factory=list)


class DedupeService:
    def __init__(self, vault_root: Path) -> None:
        self.vault_root = Path(vault_root)
        self.catalog_path = self.vault_root / "catalog" / "sounds.jsonl"
        self.sounds_root = self.vault_root / "sounds"
        self.archive_root = self.vault_root / "archive" / "dedupe"
        self.undo_log = self.vault_root / "reports" / "dedupe-undo.jsonl"

    def _folder_for(self, music_id: str) -> Path | None:
        if not self.sounds_root.exists():
            return None
        matches = sorted(p for p in self.sounds_root.glob(f"{music_id} -*") if p.is_dir())
        return matches[0] if matches else None

    def apply_decision(
        self,
        *,
        keep_music_id: str,
        duplicate_music_ids: list[str],
        dry_run: bool = False,
        now: str | None = None,
    ) -> DedupeActionResult:
        result = DedupeActionResult(keep_music_id=keep_music_id, dry_run=dry_run)
        timestamp = now or _now_iso()
        for duplicate in duplicate_music_ids:
            if not duplicate or duplicate == keep_music_id:
                continue
            folder = self._folder_for(duplicate)
            if folder is None:
                result.skipped.append(duplicate)
                continue
            archive_dest = self.archive_root / folder.name
            entry = {
                "keep_music_id": keep_music_id,
                "duplicate_music_id": duplicate,
                "original_folder": str(folder),
                "archive_folder": str(archive_dest),
                "archived_at": timestamp,
            }
            if not dry_run:
                self.archive_root.mkdir(parents=True, exist_ok=True)
                shutil.move(str(folder), str(archive_dest))
                self._append_jsonl(self.undo_log, entry)
            result.archived.append(duplicate)
            result.undo_entries.append(entry)
        if not dry_run and result.archived:
            self._drop_catalog_rows(set(result.archived))
        return result

    def apply_recorded_decisions(self, decision_store, *, dry_run: bool = False) -> list[DedupeActionResult]:
        results: list[DedupeActionResult] = []
        for row in decision_store.read_decisions():
            if str(row.get("decision")) != "duplicates":
                continue
            keep = str(row.get("keep_music_id") or "")
            duplicates = [str(x) for x in (row.get("duplicate_music_ids") or [])]
            if not keep or not duplicates:
                continue
            results.append(
                self.apply_decision(keep_music_id=keep, duplicate_music_ids=duplicates, dry_run=dry_run)
            )
        return results

    def undo(self, entry: dict) -> None:
        archive_folder = Path(entry["archive_folder"])
        original = Path(entry["original_folder"])
        if archive_folder.exists():
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(archive_folder), str(original))
        metadata_path = original / "metadata.json"
        if metadata_path.exists():
            try:
                data = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = None
            if isinstance(data, dict):
                self._append_jsonl(self.catalog_path, data)

    def _drop_catalog_rows(self, drop_ids: set[str]) -> None:
        if not self.catalog_path.exists():
            return
        kept: list[str] = []
        for line in self.catalog_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                kept.append(line)
                continue
            if isinstance(data, dict) and _catalog_music_id(data) in drop_ids:
                continue
            kept.append(line)
        tmp = self.catalog_path.with_name(f".{self.catalog_path.name}.tmp")
        tmp.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
        tmp.replace(self.catalog_path)

    @staticmethod
    def _append_jsonl(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
