import json
import subprocess

from scripts.backfill_associated_videos import (
    audit_queue,
    attempted_music_ids,
    backfill_existing_hashtag_metadata,
    repair_manifest_from_video_sidecars,
    run_capture,
    update_metadata_from_manifest,
)


def test_attempted_music_ids_reads_existing_jsonl(tmp_path):
    log = tmp_path / "associated.jsonl"
    log.write_text(
        json.dumps({"music_id": "1", "passed": False}) + "\n"
        + "not json\n"
        + json.dumps({"music_id": "2", "passed": True}) + "\n",
        encoding="utf-8",
    )

    assert attempted_music_ids(log) == {"1", "2"}


def test_run_capture_converts_timeout_to_result(tmp_path, monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["node", "capture.js"], timeout=3, output="partial", stderr="hung")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_capture(["node", "capture.js"], cwd=tmp_path, timeout=3)

    assert result.returncode == 124
    assert "timed out after 3 seconds" in result.stderr


def test_manifest_repair_uses_existing_video_sidecars(tmp_path):
    folder = tmp_path / "123 - title - artist"
    videos = folder / "videos"
    videos.mkdir(parents=True)
    mp4 = videos / "01-abc-author.mp4"
    mp4.write_bytes(b"fake mp4")
    (videos / "01-abc-author.json").write_text(
        json.dumps(
            {
                "rank": 1,
                "video_id": "abc",
                "downloaded_video_path": str(mp4),
                "download": {"ok": True},
                "description": "editor bait #CapCut #FilmTok",
            }
        ),
        encoding="utf-8",
    )
    (folder / "associated_videos_manifest.json").write_text(
        json.dumps({"source_music_id": "123", "captured_count": 0, "records": []}),
        encoding="utf-8",
    )

    repair_manifest_from_video_sidecars(folder)

    manifest = json.loads((folder / "associated_videos_manifest.json").read_text(encoding="utf-8"))
    assert manifest["captured_count"] == 1
    assert manifest["records"][0]["video_id"] == "abc"
    assert manifest["records"][0]["hashtags"] == ["capcut", "filmtok"]
    assert manifest["associated_video_hashtags"] == ["capcut", "filmtok"]


def test_audit_queue_counts_sidecar_videos_when_manifest_records_are_empty(tmp_path):
    vault = tmp_path / "vault"
    folder = vault / "sounds" / "123 - title - artist"
    videos = folder / "videos"
    videos.mkdir(parents=True)
    (folder / "metadata.json").write_text(json.dumps({"tiktok_music_id": "123"}), encoding="utf-8")
    (folder / "associated_videos_manifest.json").write_text(json.dumps({"records": [], "source_music_url": "https://example.com"}), encoding="utf-8")
    mp4 = videos / "01-abc-author.mp4"
    mp4.write_bytes(b"fake mp4")
    (videos / "01-abc-author.json").write_text(json.dumps({"rank": 1, "video_id": "abc", "downloaded_video_path": str(mp4)}), encoding="utf-8")

    queue = audit_queue(vault, minimum_videos=3)

    assert len(queue) == 1
    assert queue[0].mp4_count == 1
    assert queue[0].status == "partial_associated_videos"


def test_update_metadata_from_manifest_repairs_sidecar_records(tmp_path):
    folder = tmp_path / "123 - title - artist"
    videos = folder / "videos"
    videos.mkdir(parents=True)
    (folder / "metadata.json").write_text(json.dumps({"assets": []}), encoding="utf-8")
    (folder / "associated_videos_manifest.json").write_text(json.dumps({"records": []}), encoding="utf-8")
    mp4 = videos / "01-abc-author.mp4"
    mp4.write_bytes(b"fake mp4")
    (videos / "01-abc-author.json").write_text(
        json.dumps(
            {
                "rank": 1,
                "video_id": "abc",
                "video_url": "https://example.com/video/abc",
                "downloaded_video_path": str(mp4),
                "description": "example trend #CapCut #EditTok",
            }
        ),
        encoding="utf-8",
    )

    update_metadata_from_manifest(folder)

    metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["associated_video_count"] == 1
    assert metadata["hashtags"] == ["capcut", "edittok"]
    assert metadata["associated_video_hashtags"] == ["capcut", "edittok"]
    assert metadata["assets"][0]["asset_type"] == "associated_video"
    assert metadata["assets"][0]["hashtags"] == ["capcut", "edittok"]


def test_backfill_existing_hashtag_metadata_updates_existing_vault(tmp_path):
    vault = tmp_path / "vault"
    folder = vault / "sounds" / "123 - title - artist"
    videos = folder / "videos"
    videos.mkdir(parents=True)
    (folder / "metadata.json").write_text(json.dumps({"tiktok_music_id": "123"}), encoding="utf-8")
    mp4 = videos / "01-abc-author.mp4"
    mp4.write_bytes(b"fake mp4")
    (folder / "associated_videos_manifest.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "rank": 1,
                        "video_id": "abc",
                        "downloaded_video_path": str(mp4),
                        "description": "existing evidence #TrendTok",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    summary = backfill_existing_hashtag_metadata(vault)

    metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    assert summary == {"folders": 1, "updated": 1, "with_hashtags": 1}
    assert metadata["hashtags"] == ["trendtok"]


def test_update_metadata_rebases_stale_video_paths_before_hashtag_backfill(tmp_path):
    folder = tmp_path / "123 - title - artist"
    videos = folder / "videos"
    videos.mkdir(parents=True)
    (folder / "metadata.json").write_text(json.dumps({"tiktok_music_id": "123"}), encoding="utf-8")
    mp4 = videos / "01-abc-author.mp4"
    mp4.write_bytes(b"fake mp4")
    (folder / "associated_videos_manifest.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "rank": 1,
                        "video_id": "abc",
                        "downloaded_video_path": f"/path/to/Sound Cache/sounds/123/videos/{mp4.name}",
                        "description": "stale path but valid local file #RepairTok",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    update_metadata_from_manifest(folder)

    metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    manifest = json.loads((folder / "associated_videos_manifest.json").read_text(encoding="utf-8"))
    assert metadata["associated_video_count"] == 1
    assert metadata["hashtags"] == ["repairtok"]
    assert metadata["assets"][0]["path"] == str(mp4)
    assert manifest["records"][0]["downloaded_video_path"] == str(mp4)
