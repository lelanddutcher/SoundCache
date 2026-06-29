#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from sound_vault.vault.hashtags import aggregate_video_hashtags, enrich_video_record_hashtags, unique_hashtags


@dataclass(frozen=True)
class CaptureResult:
    returncode: int
    stdout: str
    stderr: str


def read_json(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


@dataclass(frozen=True)
class QueueItem:
    music_id: str
    folder: Path
    source_music_url: str
    captured_count: int
    mp4_count: int
    status: str


def valid_video_records(folder: Path) -> list[dict[str, Any]]:
    manifest = read_json(folder / "associated_videos_manifest.json")
    manifest_records = manifest.get("records") if isinstance(manifest.get("records"), list) else []
    sidecar_records = []
    for sidecar_path in sorted((folder / "videos").glob("*.json")):
        record = read_json(sidecar_path)
        if record:
            sidecar_records.append(record)

    records_by_video_id: dict[str, dict[str, Any]] = {}
    for record in [*manifest_records, *sidecar_records]:
        if not isinstance(record, dict):
            continue
        record = enrich_video_record_hashtags(record)
        video_path = _rebased_video_path(folder, record.get("downloaded_video_path") or record.get("path"))
        if video_path is None:
            continue
        record["downloaded_video_path"] = str(video_path)
        video_id = str(record.get("video_id") or video_path.stem).strip()
        records_by_video_id[video_id] = record
    return sorted(records_by_video_id.values(), key=lambda record: int(record.get("rank") or 9999))


def _rebased_video_path(folder: Path, value: Any) -> Path | None:
    if not value:
        return None
    try:
        path = Path(str(value))
    except (OSError, ValueError):
        return None
    if path.exists():
        return path
    name = path.name
    if not name:
        return None
    candidate = folder / "videos" / name
    try:
        return candidate if candidate.exists() else None
    except OSError:
        return None


def repair_manifest_from_video_sidecars(folder: Path) -> None:
    manifest_path = folder / "associated_videos_manifest.json"
    manifest = read_json(manifest_path)
    valid_records = valid_video_records(folder)
    if not valid_records:
        return
    existing_records = manifest.get("records") if isinstance(manifest.get("records"), list) else []
    existing_valid_count = sum(
        1 for record in existing_records if isinstance(record, dict) and Path(str(record.get("downloaded_video_path") or "")).exists()
    )
    if existing_valid_count >= len(valid_records):
        hashtags = aggregate_video_hashtags(valid_records)
        if hashtags:
            manifest["hashtags"] = list(hashtags)
            manifest["associated_video_hashtags"] = list(hashtags)
            manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    manifest["records"] = valid_records
    manifest["captured_count"] = len(valid_records)
    hashtags = aggregate_video_hashtags(valid_records)
    if hashtags:
        manifest["hashtags"] = list(hashtags)
        manifest["associated_video_hashtags"] = list(hashtags)
    manifest.setdefault("source_music_id", folder.name.split(" -", 1)[0])
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def audit_queue(vault: Path, *, minimum_videos: int) -> list[QueueItem]:
    queue: list[QueueItem] = []
    for metadata_path in sorted((vault / "sounds").glob("*/metadata.json")):
        folder = metadata_path.parent
        metadata = read_json(metadata_path)
        music_id = str(metadata.get("tiktok_music_id") or folder.name.split(" -", 1)[0]).strip()
        manifest = read_json(folder / "associated_videos_manifest.json")
        valid_records = valid_video_records(folder)
        captured_count = int(manifest.get("captured_count") or len(valid_records) or 0)
        mp4_count = len(valid_records)
        if mp4_count < minimum_videos:
            source_url = str(manifest.get("source_music_url") or metadata.get("canonical_url") or metadata.get("mobile_music_url") or f"https://www.tiktok.com/music/-{music_id}")
            status = "missing_all_associated_videos" if mp4_count == 0 else "partial_associated_videos"
            queue.append(QueueItem(music_id, folder, source_url, captured_count, mp4_count, status))
    return queue


def update_metadata_from_manifest(folder: Path) -> None:
    repair_manifest_from_video_sidecars(folder)
    metadata_path = folder / "metadata.json"
    metadata = read_json(metadata_path)
    valid_records = valid_video_records(folder)
    metadata["associated_video_count"] = len(valid_records)
    associated_video_hashtags = aggregate_video_hashtags(valid_records)
    if associated_video_hashtags:
        metadata["associated_video_hashtags"] = list(associated_video_hashtags)
        metadata["hashtags"] = list(
            unique_hashtags(
                [
                    *metadata.get("hashtags", []),
                    *associated_video_hashtags,
                ]
                if isinstance(metadata.get("hashtags"), list)
                else [metadata.get("hashtags"), *associated_video_hashtags]
            )
        )
    paths = metadata.setdefault("paths", {})
    if isinstance(paths, dict):
        paths["associated_videos_manifest"] = str(folder / "associated_videos_manifest.json")
    assets = [asset for asset in metadata.get("assets", []) if not (isinstance(asset, dict) and asset.get("asset_type") == "associated_video")]
    for record in valid_records:
        assets.append({
            "asset_type": "associated_video",
            "path": record.get("downloaded_video_path"),
            "video_id": record.get("video_id"),
            "rank": record.get("rank"),
            "source_url": record.get("video_url"),
            "description": str(record.get("description") or "")[:500],
            "hashtags": record.get("hashtags") or [],
        })
    metadata["assets"] = assets
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def backfill_existing_hashtag_metadata(vault: Path) -> dict[str, int]:
    folders = 0
    updated = 0
    with_hashtags = 0
    for metadata_path in sorted((vault / "sounds").glob("*/metadata.json")):
        folder = metadata_path.parent
        if not (folder / "associated_videos_manifest.json").exists() and not (folder / "videos").exists():
            continue
        before = read_json(metadata_path)
        update_metadata_from_manifest(folder)
        after = read_json(metadata_path)
        folders += 1
        if after.get("associated_video_hashtags"):
            with_hashtags += 1
        if before != after:
            updated += 1
    return {"folders": folders, "updated": updated, "with_hashtags": with_hashtags}


def attempted_music_ids(log_path: Path) -> set[str]:
    attempted: set[str] = set()
    if not log_path.exists():
        return attempted
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        music_id = str(row.get("music_id") or "").strip()
        if music_id:
            attempted.add(music_id)
    return attempted


def run_capture(cmd: list[str], *, cwd: Path, timeout: int) -> CaptureResult:
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(errors="replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode(errors="replace")
        message = f"timed out after {timeout} seconds"
        stderr = (stderr + "\n" + message).strip()
        return CaptureResult(returncode=124, stdout=stdout, stderr=stderr)
    return CaptureResult(result.returncode, result.stdout, result.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Retry associated-video backfill from disk audit, not checkpoint state.")
    parser.add_argument("--vault", type=Path, default=Path("/path/to/Sound Cache"))
    parser.add_argument("--project", type=Path, default=Path("/path/to/sound-organizer"))
    parser.add_argument("--capture-script", type=Path, default=Path("/path/to/sound-organizer/scripts/capture_associated_videos.cjs"))
    parser.add_argument("--minimum-videos", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0, help="0 means no limit")
    parser.add_argument("--delay", type=float, default=15.0)
    parser.add_argument("--timeout", type=int, default=300, help="Per sound capture timeout in seconds")
    parser.add_argument("--retry-attempted", action="store_true", help="Retry music IDs that already have rows in the log")
    parser.add_argument("--metadata-only", action="store_true", help="Only repair manifests/metadata from existing associated-video sidecars; do not launch capture")
    parser.add_argument("--fail-on-errors", action="store_true", help="Exit nonzero when any item remains below the requested video count")
    parser.add_argument("--log", type=Path, default=Path("/path/to/Sound Cache/workers/associated-video-backfill.jsonl"))
    args = parser.parse_args()

    if args.metadata_only:
        summary = backfill_existing_hashtag_metadata(args.vault)
        print(json.dumps(summary, indent=2))
        return 0

    args.log.parent.mkdir(parents=True, exist_ok=True)
    queue = audit_queue(args.vault, minimum_videos=args.minimum_videos)
    if not args.retry_attempted:
        attempted = attempted_music_ids(args.log)
        queue = [item for item in queue if item.music_id not in attempted]
    if args.limit > 0:
        queue = queue[: args.limit]
    ok = 0
    errors = 0
    with args.log.open("a", encoding="utf-8") as log:
        for index, item in enumerate(queue, start=1):
            cmd = ["node", str(args.capture_script), item.source_music_url, str(item.folder), item.music_id, str(args.minimum_videos)]
            result = run_capture(cmd, cwd=args.project, timeout=args.timeout)
            update_metadata_from_manifest(item.folder)
            refreshed = audit_queue(args.vault, minimum_videos=args.minimum_videos)
            still_missing = {row.music_id for row in refreshed}
            passed = result.returncode == 0 and item.music_id not in still_missing
            ok += int(passed)
            errors += int(not passed)
            row = {**asdict(item), "folder": str(item.folder), "index": index, "queued": len(queue), "returncode": result.returncode, "passed": passed, "stdout_tail": result.stdout[-1200:], "stderr_tail": result.stderr[-1200:]}
            log.write(json.dumps(row, ensure_ascii=False) + "\n")
            log.flush()
            print(f"[{index}/{len(queue)}] {item.music_id}: {'ok' if passed else 'retry-needed'}", flush=True)
            if index < len(queue) and args.delay > 0:
                time.sleep(args.delay)
    print(json.dumps({"queued": len(queue), "ok": ok, "errors": errors, "log": str(args.log)}, indent=2))
    return 1 if args.fail_on_errors and errors else 0


if __name__ == "__main__":
    sys.exit(main())
