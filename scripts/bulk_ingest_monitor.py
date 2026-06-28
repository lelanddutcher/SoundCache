#!/usr/bin/env python3
"""Bulk-ingest monitor — drive a large download test through the REAL ingest
pipeline (yt-dlp + Playwright capture + package + index) with per-item timing,
live progress, edge-case detection, and a JSON + console report.

Use a SCRATCH vault so everything actually downloads (pointing at your real vault
just marks most items 'duplicate'):

  python scripts/export_sound_pack.py --vault "/…/Sound Cache" --all --out /tmp/all.json
  python scripts/bulk_ingest_monitor.py --vault ~/SoundCacheBulkTest --pack /tmp/all.json --limit 1000

Ctrl-C finishes the current item and still writes the report. Edge cases flagged:
non-ascii / control / zero-width / empty / long titles & artists, music-id
mismatch (wrong audio for a share), tiny audio (bad capture), and slow captures.
"""
from __future__ import annotations

import argparse
import json
import signal
import time
import unicodedata
from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sound_vault.ingest.factory import build_ingest_service  # noqa: E402
from sound_vault.settings import index_path_for_vault  # noqa: E402
from sound_vault.ui.view_model import LibraryViewModel  # noqa: E402 (headless; no Qt import)


def _text_flags(label: str, value: str) -> list[str]:
    value = value or ""
    flags: list[str] = []
    if not value.strip():
        return [f"{label}:empty"]
    if any(ord(c) > 127 for c in value):
        flags.append(f"{label}:non-ascii")
    if any(unicodedata.category(c)[0] == "C" or c in "​‌‍﻿" for c in value):
        flags.append(f"{label}:control/zero-width")
    if len(value) > 120:
        flags.append(f"{label}:long({len(value)})")
    return flags


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bulk-ingest monitor for the download test")
    ap.add_argument("--vault", required=True, type=Path, help="scratch vault to download into")
    ap.add_argument("--pack", type=Path, help="sound-pack JSON to queue into the inbox first")
    ap.add_argument("--limit", type=int, default=0, help="max items to process (0 = all pending)")
    ap.add_argument("--transcribe", action="store_true", help="also run ASR (slower)")
    ap.add_argument("--slow-seconds", type=float, default=90.0)
    ap.add_argument("--report", type=Path, default=Path("bulk-ingest-report.json"))
    args = ap.parse_args(argv)

    args.vault.mkdir(parents=True, exist_ok=True)
    vm = LibraryViewModel(
        vault_root=args.vault, index_path=index_path_for_vault(args.vault),
        load_sidecars=False, sidecar_mode="summary",
    )
    if args.pack:
        s = vm.import_sound_pack(args.pack)
        print(f"queued pack: +{s['queued']} new, {s['skipped']} already, {s['rejected']} rejected")

    svc = (
        build_ingest_service(vault_root=args.vault, db=vm.db)
        if args.transcribe
        else build_ingest_service(vault_root=args.vault, db=vm.db, transcriber=None)
    )

    pending = vm.inbox.pending()
    if args.limit:
        pending = pending[: args.limit]
    total = len(pending)
    print(f"monitoring {total} item(s) into {args.vault}  (Ctrl-C = stop + report)\n")

    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("flag", True))

    results: list[dict] = []
    start = time.time()
    for i, item in enumerate(pending, 1):
        if stop["flag"]:
            print("\n[interrupted] writing report for what completed…")
            break
        t0 = time.time()
        try:
            outcome = svc.ingest_url(item.url, source=item.source, note=item.note)
            status, reason, music_id, audio = outcome.status, outcome.reason, outcome.music_id, outcome.audio_path
        except Exception as exc:  # noqa: BLE001 - keep the batch going
            status, reason, music_id, audio = "error", f"{type(exc).__name__}: {exc}", None, None
        dt = time.time() - t0

        try:
            if status in ("ingested", "duplicate"):
                vm.inbox.mark_imported(item.id)
            else:
                vm.inbox.record_failure(item.id, reason or "error", max_attempts=1)
        except Exception:  # noqa: BLE001
            pass

        flags: list[str] = []
        if status == "ingested" and music_id:
            record = vm.db.get(music_id)
            if record is not None:
                flags += _text_flags("title", record.title or "")
                flags += _text_flags("artist", record.artist or "")
            if audio and Path(audio).exists() and Path(audio).stat().st_size < 20_000:
                flags.append("tiny-audio")
            requested = (item.relay_id or "").replace("pack:", "")
            if requested.isdigit() and music_id and requested != music_id:
                flags.append(f"id-mismatch:{requested}->{music_id}")
        if dt > args.slow_seconds:
            flags.append(f"slow:{dt:.0f}s")

        results.append({"url": item.url, "status": status, "reason": reason, "music_id": music_id,
                        "seconds": round(dt, 1), "flags": flags})
        mark = (" ⚑ " + ", ".join(flags)) if flags else ""
        print(f"[{i}/{total}] {status:9} {dt:6.1f}s  …{item.url[-44:]}{mark}", flush=True)

    by_status = Counter(r["status"] for r in results)
    fails = [r for r in results if r["status"] in ("failed", "error")]
    durs = sorted(r["seconds"] for r in results) or [0.0]
    flagged = [r for r in results if r["flags"]]
    report = {
        "total": total,
        "processed": len(results),
        "elapsed_seconds": round(time.time() - start, 1),
        "by_status": dict(by_status),
        "success_rate": round(by_status.get("ingested", 0) / max(1, len(results)), 3),
        "failures_by_reason": dict(Counter((r["reason"] or "")[:100] for r in fails)),
        "edge_cases": [{"url": r["url"], "music_id": r["music_id"], "flags": r["flags"]} for r in flagged],
        "duration_seconds": {
            "median": durs[len(durs) // 2],
            "max": durs[-1],
            "slowest": [(r["seconds"], r["url"]) for r in sorted(results, key=lambda r: -r["seconds"])[:5]],
        },
        "items": results,
    }
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n========== BULK INGEST SUMMARY ==========")
    print(f"processed {len(results)}/{total} in {report['elapsed_seconds']}s")
    print("status:", dict(by_status), "| success rate:", report["success_rate"])
    if report["failures_by_reason"]:
        print("failures by reason:")
        for reason, n in report["failures_by_reason"].items():
            print(f"  {n:4}×  {reason}")
    print(f"edge-case items: {len(flagged)}")
    edge_kinds = Counter(f.split(":")[0] for r in flagged for f in r["flags"])
    for kind, n in edge_kinds.most_common():
        print(f"  {n:4}×  {kind}")
    print(f"median {report['duration_seconds']['median']}s, max {report['duration_seconds']['max']}s")
    print(f"full report → {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
