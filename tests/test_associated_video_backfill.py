import json
import subprocess

from scripts.backfill_associated_videos import attempted_music_ids, run_capture


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
