from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import ssl
import time
from typing import Any, Callable
import urllib.parse
import urllib.request

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
)


@dataclass(frozen=True)
class OEmbedEnrichmentSummary:
    source_file: str
    generated_at: str
    record_count: int
    ok_count: int
    error_count: int
    skipped_blank_ids: int
    resumed_count: int
    outputs: dict[str, str]


@dataclass(frozen=True)
class OEmbedEnrichmentResult:
    records: list[dict[str, Any]]
    summary: OEmbedEnrichmentSummary
    json_path: Path
    csv_path: Path
    checkpoint_path: Path


FetchJson = Callable[[str], dict[str, Any]]
Sleep = Callable[[float], None]


def fetch_sound_metadata(
    canonical_url: str,
    *,
    fetch_json: FetchJson | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
) -> dict[str, Any]:
    """Best-effort oEmbed lookup for a single sound URL during ingest.

    Returns ``{title, author_name, provider_name, thumbnail_url}`` (any may be
    empty) or ``{}`` on failure. oEmbed is unreliable for ``/music/`` pages, so
    callers should treat this as a fallback, not the source of truth.
    """
    target = (canonical_url or "").strip()
    if not target:
        return {}
    fetch = fetch_json or (lambda url: _fetch_oembed_json(url, user_agent=user_agent))
    api_url = "https://www.tiktok.com/oembed?url=" + urllib.parse.quote(target, safe="")
    try:
        data = fetch(api_url)
    except Exception:  # noqa: BLE001 - enrichment is best-effort, never fatal
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        "title": str(data.get("title") or "").strip(),
        "author_name": str(data.get("author_name") or "").strip(),
        "provider_name": str(data.get("provider_name") or "").strip(),
        "thumbnail_url": str(data.get("thumbnail_url") or "").strip(),
    }


def enrich_favorite_sounds_oembed(
    input_path: Path,
    out_dir: Path | None = None,
    *,
    date_label: str | None = None,
    delay_seconds: float = 0.6,
    checkpoint_every: int = 25,
    fetch_json: FetchJson | None = None,
    sleep: Sleep = time.sleep,
    user_agent: str = DEFAULT_USER_AGENT,
) -> OEmbedEnrichmentResult:
    """Enrich normalized favorite-sound rows through public TikTok oEmbed.

    This is the non-authenticated lane. It does not scrape TikTok pages, does
    not download media, and persists checkpoint files so interrupted runs can
    resume without replaying completed records.
    """
    input_path = input_path.expanduser()
    out_dir = (out_dir or input_path.parent).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    label = date_label or _label_from_input(input_path) or datetime.now(UTC).date().isoformat()
    records = _load_normalized_records(input_path)
    json_path = out_dir / f"favorite_sounds_oembed_enriched_{label}.json"
    csv_path = out_dir / f"favorite_sounds_oembed_enriched_{label}.csv"
    checkpoint_path = out_dir / f"favorite_sounds_oembed_enriched_{label}.checkpoint.json"
    existing = _load_existing_records(checkpoint_path, json_path)
    done_by_id = {
        str(row.get("tiktok_music_id") or ""): row
        for row in existing
        if row.get("tiktok_music_id") and row.get("oembed_status") in {"ok", "error"}
    }
    fetch = fetch_json or (lambda url: _fetch_oembed_json(url, user_agent=user_agent))
    enriched_records: list[dict[str, Any]] = []
    resumed_count = 0
    skipped_blank_ids = 0
    for index, row in enumerate(records, start=1):
        music_id = str(row.get("tiktok_music_id") or "")
        if not music_id:
            skipped_blank_ids += 1
            enriched = _base_oembed_row(row)
            enriched.update(
                {
                    "oembed_status": "error",
                    "oembed_error": "missing tiktok_music_id",
                    "oembed_captured_at": datetime.now(UTC).isoformat(),
                }
            )
        elif music_id in done_by_id:
            resumed_count += 1
            enriched = dict(done_by_id[music_id])
        else:
            enriched = _enrich_one(row, fetch)
            if delay_seconds > 0:
                sleep(delay_seconds)
        enriched_records.append(enriched)
        if checkpoint_every > 0 and index % checkpoint_every == 0:
            _write_payload(checkpoint_path, input_path, enriched_records)
            _write_csv_atomic(csv_path, enriched_records)
    _write_payload(json_path, input_path, enriched_records)
    _write_csv_atomic(csv_path, enriched_records)
    try:
        checkpoint_path.unlink()
    except FileNotFoundError:
        pass
    summary = _summary(
        input_path=input_path,
        records=enriched_records,
        skipped_blank_ids=skipped_blank_ids,
        resumed_count=resumed_count,
        outputs={"json": str(json_path), "csv": str(csv_path), "checkpoint": str(checkpoint_path)},
    )
    return OEmbedEnrichmentResult(
        records=enriched_records,
        summary=summary,
        json_path=json_path,
        csv_path=csv_path,
        checkpoint_path=checkpoint_path,
    )


def _load_normalized_records(input_path: Path) -> list[dict[str, Any]]:
    payload = json.loads(input_path.read_text(encoding="utf-8", errors="replace"))
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        return [dict(row) for row in payload["records"] if isinstance(row, dict)]
    if isinstance(payload, list):
        return [dict(row) for row in payload if isinstance(row, dict)]
    raise ValueError("normalized favorite-sounds import must contain a records array")


def _load_existing_records(checkpoint_path: Path, json_path: Path) -> list[dict[str, Any]]:
    for path in (checkpoint_path, json_path):
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("records"), list):
            return [dict(row) for row in payload["records"] if isinstance(row, dict)]
    return []


def _label_from_input(path: Path) -> str:
    stem = path.stem
    prefix = "favorite_sounds_import_normalized_"
    if stem.startswith(prefix):
        return stem.removeprefix(prefix)
    return ""


def _base_oembed_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out.setdefault("oembed_status", "pending")
    out.setdefault("oembed_title", "")
    out.setdefault("oembed_author_name", "")
    out.setdefault("oembed_type", "")
    out.setdefault("oembed_provider_name", "")
    out.setdefault("oembed_error", "")
    out.setdefault("oembed_captured_at", "")
    out.setdefault("oembed_url", "")
    return out


def _enrich_one(row: dict[str, Any], fetch_json: FetchJson) -> dict[str, Any]:
    out = _base_oembed_row(row)
    target = str(row.get("canonical_url_guess") or row.get("mobile_music_url") or row.get("source_link") or "")
    api_url = "https://www.tiktok.com/oembed?url=" + urllib.parse.quote(target, safe="")
    out["oembed_url"] = api_url
    try:
        data = fetch_json(api_url)
        out.update(
            {
                "oembed_status": "ok",
                "oembed_title": str(data.get("title") or ""),
                "oembed_author_name": str(data.get("author_name") or ""),
                "oembed_type": str(data.get("embed_type") or data.get("type") or ""),
                "oembed_provider_name": str(data.get("provider_name") or ""),
                "oembed_error": "",
                "oembed_captured_at": datetime.now(UTC).isoformat(),
            }
        )
    except Exception as exc:  # noqa: BLE001 - failures are expected and per-row
        out.update(
            {
                "oembed_status": "error",
                "oembed_error": repr(exc)[:500],
                "oembed_captured_at": datetime.now(UTC).isoformat(),
            }
        )
    return out


def _fetch_oembed_json(url: str, *, user_agent: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": user_agent, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=25, context=_default_ssl_context()) as response:
        payload = response.read().decode("utf-8", "replace")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("TikTok oEmbed response was not an object")
    return data


def _default_ssl_context() -> ssl.SSLContext | None:
    try:
        import certifi
    except ModuleNotFoundError:
        return None
    return ssl.create_default_context(cafile=certifi.where())


def _write_payload(path: Path, source_file: Path, records: list[dict[str, Any]]) -> None:
    payload = {
        "source_file": str(source_file),
        "generated_at": datetime.now(UTC).isoformat(),
        "record_count": len(records),
        "ok_count": sum(1 for row in records if row.get("oembed_status") == "ok"),
        "error_count": sum(1 for row in records if row.get("oembed_status") == "error"),
        "records": records,
    }
    _write_json_atomic(path, payload)


def _summary(
    *,
    input_path: Path,
    records: list[dict[str, Any]],
    skipped_blank_ids: int,
    resumed_count: int,
    outputs: dict[str, str],
) -> OEmbedEnrichmentSummary:
    return OEmbedEnrichmentSummary(
        source_file=str(input_path),
        generated_at=datetime.now(UTC).isoformat(),
        record_count=len(records),
        ok_count=sum(1 for row in records if row.get("oembed_status") == "ok"),
        error_count=sum(1 for row in records if row.get("oembed_status") == "error"),
        skipped_blank_ids=skipped_blank_ids,
        resumed_count=resumed_count,
        outputs=outputs,
    )


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    tmp_path.replace(path)


def _write_csv_atomic(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    fieldnames = _fieldnames(records)
    with tmp_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)
        handle.flush()
        os.fsync(handle.fileno())
    tmp_path.replace(path)


def _fieldnames(records: list[dict[str, Any]]) -> list[str]:
    fields: list[str] = []
    for record in records:
        for key in record:
            if key not in fields:
                fields.append(key)
    return fields or ["tiktok_music_id"]
