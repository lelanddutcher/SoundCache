import json

from sound_vault.ui.view_model import LibraryViewModel


def test_library_view_model_rebuilds_index_and_selects_preview(tmp_path):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    catalog.mkdir(parents=True)
    (catalog / "sounds.jsonl").write_text(
        json.dumps({
            "tiktok_music_id": "42",
            "tiktok_visible_title": "Kickoff Pulse",
            "source_artist": "Stadium Lab",
            "tags": ["sports", "hype"],
            "status": "approved",
            "associated_video_count": 2,
        }) + "\n",
        encoding="utf-8",
    )

    vm = LibraryViewModel(vault_root=vault, index_path=tmp_path / "index.sqlite3")
    vm.rebuild_index()

    assert vm.stats_text() == "1 sounds • 1 approved"
    assert [row.music_id for row in vm.search("hype")] == ["42"]
    assert vm.preview_for("42").title == "Kickoff Pulse"
