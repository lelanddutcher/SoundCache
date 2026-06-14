import json

from scripts.audit_duplicates import find_duplicate_candidates, find_duplicate_groups, write_outputs


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
    assert all("duration close" in candidate.reason for candidate in candidates)
    json_path, csv_path = write_outputs(candidates, tmp_path / "reports")
    assert json_path.exists()
    assert csv_path.exists()
    assert "Big Room Hit" in csv_path.read_text(encoding="utf-8")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload[0]["score"] >= 0.72
    assert payload[0]["candidates"][0]["music_id"] == "1"


def test_duplicate_audit_rejects_same_title_artist_when_transcripts_differ(tmp_path):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    sounds = vault / "sounds"
    catalog.mkdir(parents=True)
    for music_id, transcript in (
        ("1", "ice cream truck melody with kids laughing in the background"),
        ("2", "breaking news anchor says the market collapsed before sunrise"),
    ):
        folder = sounds / f"{music_id} - Same Title"
        folder.mkdir(parents=True)
        (folder / "transcript.json").write_text(json.dumps({"text": transcript}), encoding="utf-8")
    rows = [
        {
            "tiktok_music_id": "1",
            "tiktok_visible_title": "Same Title",
            "source_artist": "Same Creator",
            "duration_seconds": 12,
            "paths": {"folder": str(sounds / "1 - Same Title")},
        },
        {
            "tiktok_music_id": "2",
            "tiktok_visible_title": "same title",
            "source_artist": "Same Creator",
            "duration_seconds": 12.2,
            "paths": {"folder": str(sounds / "2 - Same Title")},
        },
    ]
    (catalog / "sounds.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    assert find_duplicate_groups(vault) == []


def test_duplicate_audit_rejects_same_title_artist_when_duration_is_vastly_different(tmp_path):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    catalog.mkdir(parents=True)
    rows = [
        {
            "tiktok_music_id": "1",
            "tiktok_visible_title": "Long Hook",
            "source_artist": "Creator",
            "duration_seconds": 7,
        },
        {
            "tiktok_music_id": "2",
            "tiktok_visible_title": "long hook",
            "source_artist": "Creator",
            "duration_seconds": 45,
        },
    ]
    (catalog / "sounds.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    assert find_duplicate_candidates(vault) == []


def test_duplicate_audit_different_thumbnails_lower_confidence_but_do_not_override_strong_audio_evidence(tmp_path):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    sounds = vault / "sounds"
    catalog.mkdir(parents=True)
    rows = []
    for music_id, image_payload in (("1", b"image-one"), ("2", b"image-two")):
        folder = sounds / f"{music_id} - Same Hook"
        folder.mkdir(parents=True)
        (folder / "artwork.jpg").write_bytes(image_payload)
        (folder / "transcript.json").write_text(
            json.dumps({"text": "same vocal hook and same punchline phrase"}),
            encoding="utf-8",
        )
        rows.append(
            {
                "tiktok_music_id": music_id,
                "tiktok_visible_title": "Same Hook",
                "source_artist": "Creator",
                "duration_seconds": 10,
                "paths": {"folder": str(folder), "artwork": str(folder / "artwork.jpg")},
            }
        )
    (catalog / "sounds.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    [group] = find_duplicate_groups(vault)

    assert "different artwork/thumbnail fingerprints" in group.reason
    assert group.score >= 0.72
