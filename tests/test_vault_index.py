import json

from sound_vault.vault.indexer import build_index


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
    assert records[0].search_text == "geekd up young jeezy & fabo football hype"
