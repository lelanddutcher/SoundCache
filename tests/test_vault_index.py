import json
from pathlib import Path

from sound_vault.vault.indexer import _existing_path, build_index


def test_build_index_reads_catalog_jsonl(tmp_path):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    catalog.mkdir(parents=True)
    (catalog / "sounds.jsonl").write_text(
        json.dumps({
            "tiktok_music_id": "7274985708375378731",
            "tiktok_visible_title": "Geekd Up",
            "source_artist": "Young Jeezy & Fabo",
            "tags": ["football", "hype"],
            "status": "approved",
        }) + "\n",
        encoding="utf-8",
    )

    records = build_index(vault)

    assert len(records) == 1
    assert records[0].music_id == "7274985708375378731"
    assert records[0].search_text == "7274985708375378731 geekd up young jeezy & fabo football hype"


def test_build_index_drops_none_tags_inside_list(tmp_path):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    catalog.mkdir(parents=True)
    (catalog / "sounds.jsonl").write_text(
        json.dumps({"tiktok_music_id": "m1", "tags": [None, " hype ", ""]}) + "\n",
        encoding="utf-8",
    )

    records = build_index(vault)

    assert records[0].tags == ("hype",)
    assert "none" not in records[0].search_text


def test_existing_path_ignores_unstatable_paths(monkeypatch):
    def raise_os_error(self):
        raise OSError("path cannot be statted")

    monkeypatch.setattr(Path, "exists", raise_os_error)

    assert _existing_path("/tmp/unstatable") is None


def test_existing_path_ignores_invalid_paths():
    assert _existing_path("bad\x00path") is None
