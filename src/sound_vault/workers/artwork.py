from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Callable

from sound_vault.workers.result import WorkerRunResult, write_worker_run

ArtworkFetcher = Callable[[dict[str, Any]], tuple[bytes, str, str] | None]


def backfill_artwork(vault_root: Path, *, fetch_artwork: ArtworkFetcher) -> WorkerRunResult:
    ok = 0
    skipped = 0
    errors: list[str] = []
    verified: list[str] = []
    events: list[dict[str, Any]] = []
    for metadata_path in sorted((vault_root / "sounds").glob("* - */metadata.json")):
        try:
            metadata = _read_json(metadata_path)
            paths = metadata.setdefault("paths", {})
            existing = paths.get("artwork")
            if existing and Path(str(existing)).exists():
                skipped += 1
                verified.append(str(existing))
                continue
            fetched = fetch_artwork(metadata)
            if fetched is None:
                skipped += 1
                _set_artwork_status(metadata, "missing")
                _write_json(metadata_path, metadata)
                continue
            blob, content_type, source_label = fetched
            suffix = _suffix(content_type)
            artwork_path = metadata_path.parent / f"sound_artwork{suffix}"
            artwork_path.write_bytes(blob)
            paths["artwork"] = str(artwork_path)
            status = "ok" if source_label == "sound_artwork" else "fallback"
            _set_artwork_status(metadata, status)
            assets = metadata.setdefault("assets", [])
            if not isinstance(assets, list):
                assets = []
                metadata["assets"] = assets
            asset = {
                "asset_type": "sound_artwork" if status == "ok" else "artwork_fallback",
                "path": str(artwork_path),
                "content_type": content_type,
                "source": source_label,
                "captured_at": _now(),
            }
            if status == "fallback":
                asset["fallback_label"] = source_label
            assets.append(asset)
            audit = metadata.setdefault("audit", {})
            if isinstance(audit, dict):
                audit["missing_artwork"] = False
                audit["artwork_source"] = source_label
            _write_json(metadata_path, metadata)
            ok += 1
            verified.extend([str(metadata_path), str(artwork_path)])
            events.append({"event": "artwork.saved", "metadata": str(metadata_path), "artwork": str(artwork_path), "status": status})
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{metadata_path}: {exc!r}")
    status = "ok" if not errors else ("partial" if ok else "error")
    result = WorkerRunResult(
        worker="artwork",
        status=status,
        counts={"total": ok + skipped + len(errors), "ok": ok, "skipped": skipped, "errors": len(errors)},
        verified_outputs=verified,
        errors=errors,
        next_actions=[] if status == "ok" else ["review artwork worker failures and rerun; never overwrite valid existing artwork with empty retries"],
    )
    write_worker_run(vault_root, result, events=events)
    return result.normalized()


def _set_artwork_status(metadata: dict[str, Any], status: str) -> None:
    capture = metadata.setdefault("media_capture", {})
    if isinstance(capture, dict):
        capture["artwork_status"] = status
        capture["last_attempt_at"] = _now()


def _suffix(content_type: str) -> str:
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"
    return ".jpg"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
