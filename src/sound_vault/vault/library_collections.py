from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any


@dataclass(frozen=True)
class LibraryBin:
    id: str
    name: str
    music_ids: tuple[str, ...]
    created_at: str = ""
    updated_at: str = ""


class LibraryCollectionsStore:
    def __init__(self, vault_root: Path) -> None:
        self.vault_root = vault_root
        self.path = vault_root / "catalog" / "library_collections.json"

    def favorites(self) -> tuple[str, ...]:
        data = self._read()
        return tuple(_dedupe_ids(data.get("favorites")))

    def is_favorite(self, music_id: str) -> bool:
        return str(music_id) in set(self.favorites())

    def toggle_favorite(self, music_id: str) -> bool:
        music_id = str(music_id).strip()
        if not music_id:
            return False
        data = self._read()
        favorites = list(_dedupe_ids(data.get("favorites")))
        if music_id in favorites:
            favorites.remove(music_id)
            is_favorite = False
        else:
            favorites.append(music_id)
            is_favorite = True
        data["favorites"] = favorites
        self._write(data)
        return is_favorite

    def add_favorite(self, music_id: str) -> None:
        self._add_to_sequence("favorites", music_id)

    def bins(self) -> tuple[LibraryBin, ...]:
        data = self._read()
        bins = data.get("bins")
        if not isinstance(bins, list):
            return ()
        out: list[LibraryBin] = []
        for row in bins:
            if not isinstance(row, dict):
                continue
            bin_id = str(row.get("id") or "").strip()
            name = str(row.get("name") or "").strip()
            if not bin_id or not name:
                continue
            out.append(
                LibraryBin(
                    id=bin_id,
                    name=name,
                    music_ids=tuple(_dedupe_ids(row.get("music_ids"))),
                    created_at=str(row.get("created_at") or ""),
                    updated_at=str(row.get("updated_at") or ""),
                )
            )
        return tuple(out)

    def create_bin(self, name: str) -> LibraryBin:
        name = " ".join(str(name or "").split()).strip()
        if not name:
            raise ValueError("bin name cannot be empty")
        data = self._read()
        bins = data.setdefault("bins", [])
        if not isinstance(bins, list):
            bins = []
            data["bins"] = bins
        existing_ids = {str(row.get("id") or "") for row in bins if isinstance(row, dict)}
        bin_id = _bin_id_for_name(name)
        if bin_id in existing_ids:
            suffix = hashlib.sha1(f"{name}-{_now()}".encode("utf-8")).hexdigest()[:6]
            bin_id = f"{bin_id}-{suffix}"
        timestamp = _now()
        row = {"id": bin_id, "name": name, "music_ids": [], "created_at": timestamp, "updated_at": timestamp}
        bins.append(row)
        self._write(data)
        return LibraryBin(id=bin_id, name=name, music_ids=(), created_at=timestamp, updated_at=timestamp)

    def add_to_bin(self, bin_id: str, music_id: str) -> bool:
        music_id = str(music_id).strip()
        if not music_id:
            return False
        data = self._read()
        bins = data.get("bins")
        if not isinstance(bins, list):
            return False
        for row in bins:
            if not isinstance(row, dict) or str(row.get("id") or "") != bin_id:
                continue
            ids = list(_dedupe_ids(row.get("music_ids")))
            if music_id not in ids:
                ids.append(music_id)
            row["music_ids"] = ids
            row["updated_at"] = _now()
            self._write(data)
            return True
        return False

    def bin_music_ids(self, bin_id: str) -> tuple[str, ...]:
        for bin_row in self.bins():
            if bin_row.id == bin_id:
                return bin_row.music_ids
        return ()

    def _add_to_sequence(self, key: str, music_id: str) -> None:
        music_id = str(music_id).strip()
        if not music_id:
            return
        data = self._read()
        values = list(_dedupe_ids(data.get(key)))
        if music_id not in values:
            values.append(music_id)
        data[key] = values
        self._write(data)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "favorites": [], "bins": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "favorites": [], "bins": []}
        if not isinstance(data, dict):
            return {"version": 1, "favorites": [], "bins": []}
        data.setdefault("version", 1)
        data.setdefault("favorites", [])
        data.setdefault("bins", [])
        return data

    def _write(self, data: dict[str, Any]) -> None:
        data["version"] = 1
        data["favorites"] = list(_dedupe_ids(data.get("favorites")))
        if not isinstance(data.get("bins"), list):
            data["bins"] = []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f".{self.path.name}.tmp")
        tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp_path, self.path)


def _dedupe_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    seen: set[str] = set()
    out: list[str] = []
    for item in value:
        music_id = str(item or "").strip()
        if music_id and music_id not in seen:
            seen.add(music_id)
            out.append(music_id)
    return tuple(out)


def _bin_id_for_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "bin"
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"bin-{slug[:36]}-{digest}"


def _now() -> str:
    return datetime.now(UTC).isoformat()
