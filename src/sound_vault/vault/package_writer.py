from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
from typing import Any

from sound_vault.vault.indexer import resolve_vault_root


@dataclass(frozen=True)
class PackageImportSummary:
    source_file: str
    record_count: int
    created_count: int
    updated_count: int
    skipped_blank_ids: int
    failed_count: int
    metadata_only_count: int
    missing_audio: int
    missing_artwork: int
    missing_transcript: int
    missing_videos: int
    missing_popularity: int
    catalog_jsonl: str
    catalog_csv: str
    failure_log: str


@dataclass(frozen=True)
class PackageImportResult:
    summary: PackageImportSummary
    catalog_jsonl: Path
    catalog_csv: Path
    failure_log: Path
    records: list[dict[str, Any]]


def package_imported_sounds(
    input_path: Path,
    vault_root: Path,
    *,
    force: bool = False,
) -> PackageImportResult:
    """Create durable metadata-only vault packages from imported/enriched rows."""
    input_path = input_path.expanduser()
    vault_root = resolve_vault_root(vault_root).expanduser()
    records = _load_records(input_path)
    sounds_root = vault_root / "sounds"
    catalog_root = vault_root / "catalog"
    sounds_root.mkdir(parents=True, exist_ok=True)
    catalog_root.mkdir(parents=True, exist_ok=True)
    failures: list[dict[str, str]] = []
    packaged_records: list[dict[str, Any]] = []
    created = 0
    updated = 0
    skipped_blank = 0
    for row in records:
        music_id = str(row.get("tiktok_music_id") or row.get("music_id") or "").strip()
        if not music_id:
            skipped_blank += 1
            failures.append({"music_id": "", "error": "missing tiktok_music_id"})
            continue
        try:
            folder = _package_folder_for_row(vault_root, row)
            metadata_path = folder / "metadata.json"
            existing = _read_json(metadata_path)
            if existing and not force:
                metadata = _merge_metadata(existing, _metadata_from_row(row, folder))
                updated += 1
            else:
                folder.mkdir(parents=True, exist_ok=True)
                metadata = _metadata_from_row(row, folder)
                created += 1
            _write_json_atomic(metadata_path, metadata)
            packaged_records.append(metadata)
        except Exception as exc:  # noqa: BLE001 - per-row failure should not stop batch
            failures.append({"music_id": music_id, "error": repr(exc)[:500]})
    catalog_jsonl = catalog_root / "sounds.jsonl"
    catalog_csv = catalog_root / "sounds.csv"
    _upsert_catalog(catalog_jsonl, packaged_records)
    catalog_rows = _read_catalog_rows(catalog_jsonl)
    _write_csv_atomic(catalog_csv, catalog_rows)
    failure_log = vault_root / "workers" / "failed" / f"package_import_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.csv"
    _write_failures(failure_log, failures)
    final_records = [row for row in catalog_rows if str(row.get("tiktok_music_id") or row.get("music_id") or "")]
    summary = PackageImportSummary(
        source_file=str(input_path),
        record_count=len(records),
        created_count=created,
        updated_count=updated,
        skipped_blank_ids=skipped_blank,
        failed_count=len(failures),
        metadata_only_count=sum(1 for row in final_records if _is_metadata_only(row)),
        missing_audio=sum(1 for row in final_records if not _has_path(row, "audio")),
        missing_artwork=sum(1 for row in final_records if not _has_path(row, "artwork")),
        missing_transcript=sum(1 for row in final_records if not _has_path(row, "transcript")),
        missing_videos=sum(1 for row in final_records if _associated_video_count(row) == 0),
        missing_popularity=sum(1 for row in final_records if row.get("usage_count") in (None, "")),
        catalog_jsonl=str(catalog_jsonl),
        catalog_csv=str(catalog_csv),
        failure_log=str(failure_log),
    )
    return PackageImportResult(
        summary=summary,
        catalog_jsonl=catalog_jsonl,
        catalog_csv=catalog_csv,
        failure_log=failure_log,
        records=packaged_records,
    )


def sanitize_filename_component(value: Any, *, max_len: int = 80) -> str:
    text = str(value or "").replace("♬", "").strip()
    text = re.sub(r'[/:*?"<>|]', "", text)
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    text = re.sub(r"\s+", " ", text).strip().strip(".")
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0].strip()
    return text or "Unknown"


def _load_records(input_path: Path) -> list[dict[str, Any]]:
    payload = json.loads(input_path.read_text(encoding="utf-8", errors="replace"))
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        return [dict(row) for row in payload["records"] if isinstance(row, dict)]
    if isinstance(payload, list):
        return [dict(row) for row in payload if isinstance(row, dict)]
    raise ValueError("package input must contain a records array")


def _package_folder_for_row(vault_root: Path, row: dict[str, Any]) -> Path:
    music_id = str(row.get("tiktok_music_id") or row.get("music_id") or "").strip()
    sounds_root = vault_root / "sounds"
    for candidate in sorted(sounds_root.glob(f"{music_id} -*")):
        if candidate.is_dir() and (candidate / "metadata.json").exists():
            return candidate
    title = _title_from_row(row)
    author = _author_from_row(row)
    return sounds_root / f"{music_id} - {sanitize_filename_component(title, max_len=40)} - {sanitize_filename_component(author, max_len=30)}"


def _title_from_row(row: dict[str, Any]) -> str:
    return str(
        row.get("oembed_title")
        or row.get("tiktok_visible_title")
        or row.get("title")
        or row.get("source_title")
        or "Unknown"
    ).replace("♬", "").strip()


def _author_from_row(row: dict[str, Any]) -> str:
    return str(
        row.get("oembed_author_name")
        or row.get("source_artist")
        or row.get("artist")
        or row.get("tiktok_author_or_copyright")
        or "Unknown"
    ).strip()


def _metadata_from_row(row: dict[str, Any], folder: Path) -> dict[str, Any]:
    music_id = str(row.get("tiktok_music_id") or row.get("music_id") or "").strip()
    oembed_ok = row.get("oembed_status") == "ok"
    title = _title_from_row(row)
    author = _author_from_row(row)
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    source_confidence = "tiktok_oembed_only" if oembed_ok else "tiktok_export_metadata_only"
    tags = ["favorite_sound", "metadata_only"]
    if oembed_ok:
        tags.append("oembed_enriched")
    elif row.get("oembed_status"):
        tags.append("oembed_failed")
    paths = {
        "folder": str(folder),
        "audio": None,
        "artwork": None,
        "transcript": None,
        "associated_videos_manifest": None,
        "page_snapshot": None,
    }
    return {
        "vault_version": 1,
        "tiktok_music_id": music_id,
        "music_id": music_id,
        "canonical_url": row.get("canonical_url_guess") or row.get("canonical_url") or "",
        "mobile_music_url": row.get("mobile_music_url") or "",
        "source_link": row.get("source_link") or "",
        "saved_at": row.get("saved_at") or "",
        "ingest_source": row.get("source") or "tiktok_data_export_favorite_sounds",
        "tiktok_visible_title": title,
        "tiktok_author_or_copyright": author,
        "source_artist": author,
        "usage_count": None,
        "associated_video_count": 0,
        "source_provider": row.get("oembed_provider_name") or ("TikTok oEmbed" if oembed_ok else ""),
        "source_confidence": source_confidence,
        "tags": tags,
        "status": "metadata_only",
        "package_status": "metadata_only",
        "audit": {
            "metadata_only": True,
            "missing_audio": True,
            "missing_artwork": True,
            "missing_transcript": True,
            "missing_videos": True,
            "missing_popularity": True,
        },
        "paths": paths,
        "assets": [],
        "evidence": {
            "oembed_status": row.get("oembed_status") or "not_run",
            "oembed_type": row.get("oembed_type") or "",
            "oembed_error": row.get("oembed_error") or "",
            "oembed_captured_at": row.get("oembed_captured_at") or "",
        },
        "packaged_at": now,
    }


def _merge_metadata(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if key == "paths":
            paths = dict(incoming.get("paths") if isinstance(incoming.get("paths"), dict) else {})
            if isinstance(existing.get("paths"), dict):
                paths.update({k: v for k, v in existing["paths"].items() if v not in (None, "", [], {})})
            merged["paths"] = paths
            continue
        if key == "tags":
            existing_tags = existing.get("tags") if isinstance(existing.get("tags"), list) else []
            incoming_tags = incoming.get("tags") if isinstance(incoming.get("tags"), list) else []
            merged["tags"] = list(dict.fromkeys([*incoming_tags, *existing_tags]))
            continue
        if key in {"audit", "evidence"} and isinstance(value, dict):
            old = existing.get(key) if isinstance(existing.get(key), dict) else {}
            merged[key] = {**value, **old}
            continue
        if key == "package_status" and _has_path(merged, "audio"):
            continue
        if merged.get(key) in (None, "", [], {}) and value not in (None, "", [], {}):
            merged[key] = value
    audit = dict(merged.get("audit") if isinstance(merged.get("audit"), dict) else {})
    audit.update(
        {
            "missing_audio": not _has_path(merged, "audio"),
            "missing_artwork": not _has_path(merged, "artwork"),
            "missing_transcript": not _has_path(merged, "transcript"),
            "missing_videos": _associated_video_count(merged) == 0,
            "missing_popularity": merged.get("usage_count") in (None, ""),
        }
    )
    audit["metadata_only"] = (
        audit["missing_audio"]
        and audit["missing_artwork"]
        and audit["missing_transcript"]
        and audit["missing_videos"]
    )
    merged["audit"] = audit
    if not audit["metadata_only"] and merged.get("package_status") == "metadata_only":
        merged["package_status"] = "packaged_existing"
    merged.setdefault("package_status", "metadata_only")
    return merged


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _upsert_catalog(catalog_jsonl: Path, records: list[dict[str, Any]]) -> None:
    existing_rows = _read_catalog_rows(catalog_jsonl)
    incoming_by_id = {_music_id(row): row for row in records if _music_id(row)}
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in existing_rows:
        music_id = _music_id(row)
        if music_id and music_id in incoming_by_id:
            output.append(incoming_by_id[music_id])
            seen.add(music_id)
        else:
            output.append(row)
    for music_id, row in incoming_by_id.items():
        if music_id not in seen:
            output.append(row)
    _write_jsonl_atomic(catalog_jsonl, output)


def _read_catalog_rows(catalog_jsonl: Path) -> list[dict[str, Any]]:
    if not catalog_jsonl.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with catalog_jsonl.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
    except OSError:
        return []
    return rows


def _music_id(row: dict[str, Any]) -> str:
    return str(row.get("tiktok_music_id") or row.get("music_id") or "").strip()


def _is_metadata_only(row: dict[str, Any]) -> bool:
    if row.get("package_status") == "metadata_only" or row.get("status") == "metadata_only":
        return True
    audit = row.get("audit")
    return bool(isinstance(audit, dict) and audit.get("metadata_only"))


def _has_path(row: dict[str, Any], key: str) -> bool:
    paths = row.get("paths")
    if isinstance(paths, dict) and paths.get(key):
        return True
    return False


def _associated_video_count(row: dict[str, Any]) -> int:
    try:
        return int(row.get("associated_video_count") or 0)
    except (TypeError, ValueError):
        return 0


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    tmp_path.replace(path)


def _write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    tmp_path.replace(path)


def _write_csv_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    fields = _fieldnames(rows)
    with tmp_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())
    tmp_path.replace(path)


def _write_failures(path: Path, failures: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["music_id", "error"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(failures)


def _fieldnames(rows: list[dict[str, Any]]) -> list[str]:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    return fields or ["tiktok_music_id"]


def package_summary_rows(vault_root: Path) -> list[tuple[str, str]]:
    vault_root = resolve_vault_root(vault_root)
    rows = _read_catalog_rows(vault_root / "catalog" / "sounds.jsonl")
    return [
        ("Packaged catalog rows", f"{len(rows):,}"),
        ("Metadata-only packages", f"{sum(1 for row in rows if _is_metadata_only(row)):,}"),
        ("Package audit: missing audio", f"{sum(1 for row in rows if not _has_path(row, 'audio')):,}"),
        ("Package audit: missing artwork", f"{sum(1 for row in rows if not _has_path(row, 'artwork')):,}"),
        ("Package audit: missing transcripts", f"{sum(1 for row in rows if not _has_path(row, 'transcript')):,}"),
        ("Package audit: missing videos", f"{sum(1 for row in rows if _associated_video_count(row) == 0):,}"),
        ("Package audit: missing popularity", f"{sum(1 for row in rows if row.get('usage_count') in (None, '')):,}"),
    ]


def latest_import_artifact_rows(vault_root: Path) -> list[tuple[str, str]]:
    imports_root = resolve_vault_root(vault_root) / "catalog" / "imports"
    if not imports_root.exists():
        return [("Import artifacts", "none yet")]
    patterns = [
        ("Latest normalized import", "favorite_sounds_import_normalized_*.json"),
        ("Latest import summary", "favorite_sounds_import_summary_*.json"),
        ("Latest oEmbed enrichment", "favorite_sounds_oembed_enriched_*.json"),
    ]
    rows: list[tuple[str, str]] = []
    for label, pattern in patterns:
        matches = sorted(imports_root.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
        rows.append((label, matches[0].name if matches else "missing"))
    return rows
