import json

from scripts.audit_associated_videos import audit_vault, write_outputs


def test_associated_video_audit_queues_zero_and_partial_manifests(tmp_path):
    vault = tmp_path / "vault"
    ok = vault / "sounds" / "1 - ok"
    missing = vault / "sounds" / "2 - missing"
    partial = vault / "sounds" / "3 - partial"
    for folder in (ok, missing, partial):
        (folder / "videos").mkdir(parents=True)
        (folder / "metadata.json").write_text(
            json.dumps({"tiktok_music_id": folder.name.split(" ", 1)[0], "canonical_url": "https://www.tiktok.com/music/test"}),
            encoding="utf-8",
        )
    (ok / "associated_videos_manifest.json").write_text(
        json.dumps({"captured_count": 3, "requested_max_videos": 3, "records": [{}, {}, {}]}),
        encoding="utf-8",
    )
    for idx in range(3):
        (ok / "videos" / f"{idx}.mp4").write_bytes(b"video")
    (missing / "associated_videos_manifest.json").write_text(
        json.dumps({"captured_count": 0, "requested_max_videos": 3, "records": []}),
        encoding="utf-8",
    )
    (partial / "associated_videos_manifest.json").write_text(
        json.dumps({"captured_count": 1, "requested_max_videos": 3, "records": [{}]}),
        encoding="utf-8",
    )

    rows = audit_vault(vault, minimum_videos=3)
    statuses = {row.music_id: row.status for row in rows}

    assert statuses == {
        "1": "ok",
        "2": "missing_all_associated_videos",
        "3": "partial_associated_videos",
    }

    output_dir = tmp_path / "out"
    write_outputs(rows, output_dir)
    queued = (output_dir / "associated_video_backfill_queue.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(queued) == 2
    assert "missing_all_associated_videos" in queued[0]
