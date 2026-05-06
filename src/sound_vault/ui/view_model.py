from __future__ import annotations

from pathlib import Path

from sound_vault.db.index_db import IndexDatabase
from sound_vault.vault.indexer import SoundRecord, build_index


class LibraryViewModel:
    def __init__(self, *, vault_root: Path, index_path: Path) -> None:
        self.vault_root = vault_root
        self.index_path = index_path
        self.db = IndexDatabase(index_path)
        self._records_by_id: dict[str, SoundRecord] = {}

    def rebuild_index(self) -> None:
        records = build_index(self.vault_root)
        self._records_by_id = {record.music_id: record for record in records}
        self.db.rebuild(records)

    def search(self, query: str) -> list[SoundRecord]:
        records = self.db.search(query)
        for record in records:
            if record.music_id not in self._records_by_id:
                self._records_by_id[record.music_id] = record
        return records

    def preview_for(self, music_id: str) -> SoundRecord:
        if music_id not in self._records_by_id:
            results = self.db.search(music_id, limit=1)
            if not results:
                raise KeyError(music_id)
            self._records_by_id[results[0].music_id] = results[0]
        return self._records_by_id[music_id]

    def stats_text(self) -> str:
        stats = self.db.stats()
        return f"{stats.total_sounds:,} sounds • {stats.approved_sounds:,} approved"
