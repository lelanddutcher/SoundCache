#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import time

from sound_vault.workers.popularity_backfill import (
    UsageBackfillResult,
    music_url_for_folder,
    sound_folders_missing_usage,
    update_metadata_usage_count,
    utc_now_iso,
)


def run_capture(script: Path, url: str, storage_state: Path | None, *, cwd: Path) -> dict:
    cmd = ["node", str(script), url]
    if storage_state is not None:
        cmd.append(str(storage_state))
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=75, check=False)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {"ok": False, "error": (result.stderr or result.stdout)[-1000:]}
    if result.returncode != 0 and not payload.get("error"):
        payload["error"] = result.stderr[-1000:]
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill TikTok music-page usage_count labels into metadata.json.")
    parser.add_argument("--vault", type=Path, default=Path("/path/to/Sound Cache"))
    parser.add_argument("--project", type=Path, default=Path("/path/to/sound-organizer"))
    parser.add_argument("--storage-state", type=Path, default=Path("/path/to/sound-organizer/auth/tiktok.storageState.json"))
    parser.add_argument("--script", type=Path, default=Path(__file__).with_name("capture_usage_count.cjs"))
    parser.add_argument("--limit", type=int, default=0, help="0 means no limit")
    parser.add_argument("--delay", type=float, default=8.0)
    parser.add_argument("--log", type=Path, default=Path("/path/to/Sound Cache/workers/usage-count-backfill.jsonl"))
    args = parser.parse_args()

    args.log.parent.mkdir(parents=True, exist_ok=True)
    folders = sound_folders_missing_usage(args.vault)
    if args.limit > 0:
        folders = folders[: args.limit]
    storage_state = args.storage_state if args.storage_state.exists() else None
    ok = 0
    errors = 0
    with args.log.open("a", encoding="utf-8") as log:
        for index, folder in enumerate(folders, start=1):
            music_id, url = music_url_for_folder(folder)
            captured_at = utc_now_iso()
            if not music_id or not url:
                result = UsageBackfillResult(music_id=music_id, usage_count=None, usage_count_label="", source="metadata", captured_at=captured_at, ok=False, error="missing music id or url")
            else:
                payload = run_capture(args.script, url, storage_state, cwd=args.project)
                result = UsageBackfillResult(
                    music_id=music_id,
                    usage_count=payload.get("usage_count"),
                    usage_count_label=str(payload.get("usage_count_label") or ""),
                    source=str(payload.get("source") or "music_page_dom"),
                    captured_at=captured_at,
                    ok=bool(payload.get("ok") and payload.get("usage_count") is not None),
                    error=str(payload.get("error") or ""),
                )
                if result.ok:
                    update_metadata_usage_count(folder, result)
                    ok += 1
                else:
                    errors += 1
            log.write(json.dumps({"folder": str(folder), **asdict(result)}, ensure_ascii=False) + "\n")
            log.flush()
            print(f"[{index}/{len(folders)}] {music_id}: {'ok' if result.ok else 'miss'} {result.usage_count_label or result.error}", flush=True)
            if index < len(folders) and args.delay > 0:
                time.sleep(args.delay)
    print(json.dumps({"queued": len(folders), "ok": ok, "errors": errors, "log": str(args.log)}, indent=2))
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
