import json

from scripts.audit_duplicates import find_duplicate_candidates, write_outputs


def test_duplicate_audit_groups_same_title_artist_without_deleting(tmp_path):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    catalog.mkdir(parents=True)
    rows = [
        {
            "tiktok_music_id": "1",
            "tiktok_visible_title": "Big Room Hit!",
            "source_artist": "DJ Test",
            "duration_seconds": 12.1,
            "paths": {"folder": "/tmp/one"},
        },
        {
            "tiktok_music_id": "2",
            "tiktok_visible_title": "big room hit",
            "source_artist": "DJ Test",
            "duration_seconds": 12.3,
            "paths": {"folder": "/tmp/two"},
        },
        {
            "tiktok_music_id": "3",
            "tiktok_visible_title": "Other Song",
            "source_artist": "DJ Test",
        },
    ]
    (catalog / "sounds.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    candidates = find_duplicate_candidates(vault)

    assert [candidate.music_id for candidate in candidates] == ["1", "2"]
    assert {candidate.reason for candidate in candidates} == {"same normalized title+artist"}
    json_path, csv_path = write_outputs(candidates, tmp_path / "reports")
    assert json_path.exists()
    assert csv_path.exists()
    assert "Big Room Hit" in csv_path.read_text(encoding="utf-8")
