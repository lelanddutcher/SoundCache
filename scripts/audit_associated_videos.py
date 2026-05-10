#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AssociatedVideoAuditRow:
    music_id: str
    folder: Path
    source_music_url: str
    captured_count: int
    requested_max_videos: int
    mp4_count: int
    status: str


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def audit_sound_folder(folder: Path, *, minimum_videos: int) -> AssociatedVideoAuditRow | None:
    metadata = _read_json(folder / "metadata.json")
    music_id = str(metadata.get("tiktok_music_id") or folder.name.split(" -", 1)[0]).strip()
    if not music_id:
        return None
    manifest_path = folder / "associated_videos_manifest.json"
    manifest = _read_json(manifest_path)
    records = manifest.get("records") if isinstance(manifest.get("records"), list) else []
    captured_count = int(manifest.get("captured_count") or len(records) or 0)
    requested_max_videos = int(manifest.get("requested_max_videos") or minimum_videos)
    source_music_url = str(
        manifest.get("source_music_url")
        or metadata.get("canonical_url")
        or metadata.get("mobile_music_url")
        or f"https://www.tiktok.com/music/-{music_id}"
    )
    mp4_count = len(list((folder / "videos").glob("*.mp4"))) if (folder / "videos").exists() else 0
    if captured_count >= minimum_videos and mp4_count >= minimum_videos:
        status = "ok"
    elif captured_count == 0 and mp4_count == 0:
        status = "missing_all_associated_videos"
    elif captured_count >= minimum_videos and mp4_count < minimum_videos:
        status = "manifest_without_enough_downloads"
    else:
        status = "partial_associated_videos"
    return AssociatedVideoAuditRow(
        music_id=music_id,
        folder=folder,
        source_music_url=source_music_url,
        captured_count=captured_count,
        requested_max_videos=requested_max_videos,
        mp4_count=mp4_count,
        status=status,
    )


def audit_vault(vault_root: Path, *, minimum_videos: int) -> list[AssociatedVideoAuditRow]:
    sounds_root = vault_root / "sounds"
    return [
        row
        for folder in sorted(sounds_root.iterdir())
        if folder.is_dir()
        for row in [audit_sound_folder(folder, minimum_videos=minimum_videos)]
        if row is not None
    ]


def write_outputs(rows: list[AssociatedVideoAuditRow], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    queue_path = output_dir / "associated_video_backfill_queue.jsonl"
    summary_path = output_dir / "associated_video_audit_summary.json"
    csv_path = output_dir / "associated_video_audit.csv"

    missing = [row for row in rows if row.status != "ok"]
    with queue_path.open("w", encoding="utf-8") as fh:
        for row in missing:
            fh.write(
                json.dumps(
                    {
                        "music_id": row.music_id,
                        "folder": str(row.folder),
                        "source_music_url": row.source_music_url,
                        "captured_count": row.captured_count,
                        "mp4_count": row.mp4_count,
                        "status": row.status,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "music_id",
                "folder",
                "source_music_url",
                "captured_count",
                "requested_max_videos",
                "mp4_count",
                "status",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({**row.__dict__, "folder": str(row.folder)})
    summary: dict[str, int] = {"total": len(rows), "needs_backfill": len(missing)}
    for row in rows:
        summary[row.status] = summary.get(row.status, 0) + 1
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit TikTok Sound Vault associated-video coverage and queue misses.")
    parser.add_argument("--vault", type=Path, default=Path("/nas/TikTok Sound Vault"))
    parser.add_argument("--minimum-videos", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=Path("/nas/TikTok Sound Vault/workers/backfill"))
    args = parser.parse_args()

    rows = audit_vault(args.vault, minimum_videos=args.minimum_videos)
    write_outputs(rows, args.output_dir)
    summary = {"total": len(rows)}
    for row in rows:
        summary[row.status] = summary.get(row.status, 0) + 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
