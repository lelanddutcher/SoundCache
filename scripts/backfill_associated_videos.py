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


def audit_queue(vault: Path, *, minimum_videos: int) -> list[QueueItem]:
    queue: list[QueueItem] = []
    for metadata_path in sorted((vault / "sounds").glob("*/metadata.json")):
        folder = metadata_path.parent
        metadata = read_json(metadata_path)
        music_id = str(metadata.get("tiktok_music_id") or folder.name.split(" -", 1)[0]).strip()
        manifest = read_json(folder / "associated_videos_manifest.json")
        records = manifest.get("records") if isinstance(manifest.get("records"), list) else []
        captured_count = int(manifest.get("captured_count") or len(records) or 0)
        mp4_count = sum(1 for path in records if isinstance(path, dict) and Path(str(path.get("downloaded_video_path") or "")).exists())
        if mp4_count < minimum_videos:
            source_url = str(manifest.get("source_music_url") or metadata.get("canonical_url") or metadata.get("mobile_music_url") or f"https://www.tiktok.com/music/-{music_id}")
            status = "missing_all_associated_videos" if mp4_count == 0 else "partial_associated_videos"
            queue.append(QueueItem(music_id, folder, source_url, captured_count, mp4_count, status))
    return queue


def update_metadata_from_manifest(folder: Path) -> None:
    metadata_path = folder / "metadata.json"
    metadata = read_json(metadata_path)
    manifest = read_json(folder / "associated_videos_manifest.json")
    records = manifest.get("records") if isinstance(manifest.get("records"), list) else []
    valid_records = [record for record in records if isinstance(record, dict) and Path(str(record.get("downloaded_video_path") or "")).exists()]
    metadata["associated_video_count"] = len(valid_records)
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
        })
    metadata["assets"] = assets
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Retry associated-video backfill from disk audit, not checkpoint state.")
    parser.add_argument("--vault", type=Path, default=Path("/nas/TikTok Sound Vault"))
    parser.add_argument("--project", type=Path, default=Path("/nas/Projects/Tiktok Sound Organizer"))
    parser.add_argument("--capture-script", type=Path, default=Path("/nas/Projects/Tiktok Sound Organizer/scripts/capture_associated_videos.cjs"))
    parser.add_argument("--minimum-videos", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0, help="0 means no limit")
    parser.add_argument("--delay", type=float, default=15.0)
    parser.add_argument("--log", type=Path, default=Path("/nas/TikTok Sound Vault/workers/associated-video-backfill.jsonl"))
    args = parser.parse_args()

    args.log.parent.mkdir(parents=True, exist_ok=True)
    queue = audit_queue(args.vault, minimum_videos=args.minimum_videos)
    if args.limit > 0:
        queue = queue[: args.limit]
    ok = 0
    errors = 0
    with args.log.open("a", encoding="utf-8") as log:
        for index, item in enumerate(queue, start=1):
            cmd = ["node", str(args.capture_script), item.source_music_url, str(item.folder), item.music_id, str(args.minimum_videos)]
            result = subprocess.run(cmd, cwd=args.project, capture_output=True, text=True, timeout=300, check=False)
            update_metadata_from_manifest(item.folder)
            after = audit_queue(args.vault, minimum_videos=args.minimum_videos)
            still_missing = {row.music_id for row in after}
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
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
