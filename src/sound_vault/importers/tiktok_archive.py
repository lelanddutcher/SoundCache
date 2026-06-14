from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
from typing import Any
import urllib.parse

MUSIC_ID_RE = re.compile(
    r"(?:/music/|music[-/]|share/music/)(?:[^/?#\s]*?-)?(\d+)(?:\.html)?",
    re.IGNORECASE,
)
FAVORITE_SOUNDS_SOURCE = "tiktok_data_export_favorite_sounds"


@dataclass(frozen=True)
class FavoriteSoundRecord:
    import_index: int
    saved_at: str
    source_link: str
    tiktok_music_id: str
    canonical_url_guess: str
    mobile_music_url: str
    source: str = FAVORITE_SOUNDS_SOURCE
    resolve_status: str = "unresolved"
    vault_match_status: str = "not_checked"
    vault_match_reason: str = ""
    vault_match_music_id: str = ""
    vault_match_folder: str = ""
    vault_match_url: str = ""


@dataclass(frozen=True)
class FavoriteSoundImportSummary:
    source_file: str
    source_size_bytes: int
    record_count: int
    unique_music_ids: int
    blank_ids: int
    duplicate_music_ids: int
    malformed_rows: int
    already_in_vault: int
    new_to_vault: int
    ambiguous_matches: int
    vault_match_counts: dict[str, int]
    date_min: str
    date_max: str
    by_year: dict[str, int]
    outputs: dict[str, str]


@dataclass(frozen=True)
class FavoriteSoundImportResult:
    records: list[FavoriteSoundRecord]
    summary: FavoriteSoundImportSummary
    json_path: Path
    csv_path: Path
    summary_path: Path


def repair_tiktok_favorite_sounds_json(raw: str) -> str:
    text = raw.strip().lstrip("\ufeff")
    if text.startswith("{"):
        return text
    if text.startswith('Favorite Sounds"') or text.startswith('"Favorite Sounds"'):
        if not text.startswith('"'):
            text = '"' + text
        text = re.sub(r",\s*$", "", text)
        return "{" + text + "}"
    raise ValueError("Unrecognized TikTok favorite sounds export shape; inspect before importing")


def extract_music_id(url: str) -> str:
    match = MUSIC_ID_RE.search(str(url or ""))
    return match.group(1) if match else ""


def normalize_favorite_sound_row(row: dict[str, Any], import_index: int) -> FavoriteSoundRecord:
    saved_at = str(row.get("Date") or row.get("date") or row.get("saved_at") or "").strip()
    source_link = str(row.get("Link") or row.get("link") or row.get("source_link") or "").strip()
    music_id = extract_music_id(source_link)
    return FavoriteSoundRecord(
        import_index=import_index,
        saved_at=saved_at,
        source_link=source_link,
        tiktok_music_id=music_id,
        canonical_url_guess=f"https://www.tiktok.com/music/-{music_id}" if music_id else "",
        mobile_music_url=f"https://m.tiktok.com/h5/share/music/{music_id}.html" if music_id else "",
    )


def load_favorite_sound_rows(input_path: Path) -> tuple[list[FavoriteSoundRecord], int]:
    raw = input_path.read_text(encoding="utf-8", errors="replace")
    parsed = json.loads(repair_tiktok_favorite_sounds_json(raw))
    rows = _favorite_sound_list(parsed)
    records: list[FavoriteSoundRecord] = []
    malformed_rows = 0
    for import_index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            malformed_rows += 1
            continue
        records.append(normalize_favorite_sound_row(row, import_index))
    return records, malformed_rows


def write_normalized_favorite_sounds_import(
    input_path: Path,
    out_dir: Path,
    *,
    date_label: str | None = None,
    vault_root: Path | None = None,
) -> FavoriteSoundImportResult:
    input_path = input_path.expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    label = date_label or datetime.now(UTC).date().isoformat()
    records, malformed_rows = load_favorite_sound_rows(input_path)
    if vault_root is not None:
        records = annotate_vault_matches(records, vault_root)
    json_path = out_dir / f"favorite_sounds_import_normalized_{label}.json"
    csv_path = out_dir / f"favorite_sounds_import_normalized_{label}.csv"
    summary_path = out_dir / f"favorite_sounds_import_summary_{label}.json"
    summary = _build_summary(
        input_path=input_path,
        records=records,
        malformed_rows=malformed_rows,
        outputs={"json": str(json_path), "csv": str(csv_path)},
    )
    payload = {
        "source_file": str(input_path),
        "generated_at": datetime.now(UTC).isoformat(),
        "record_count": len(records),
        "unique_music_ids": summary.unique_music_ids,
        "blank_ids": summary.blank_ids,
        "duplicate_music_ids": summary.duplicate_music_ids,
        "malformed_rows": malformed_rows,
        "already_in_vault": summary.already_in_vault,
        "new_to_vault": summary.new_to_vault,
        "ambiguous_matches": summary.ambiguous_matches,
        "vault_match_counts": summary.vault_match_counts,
        "records": [asdict(record) for record in records],
    }
    _write_json_atomic(json_path, payload)
    _write_csv_atomic(csv_path, records)
    _write_json_atomic(summary_path, asdict(summary))
    return FavoriteSoundImportResult(
        records=records,
        summary=summary,
        json_path=json_path,
        csv_path=csv_path,
        summary_path=summary_path,
    )


@dataclass(frozen=True)
class _VaultMatchEntry:
    music_id: str
    folder: str = ""
    urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class _VaultMatch:
    status: str
    reason: str
    music_id: str = ""
    folder: str = ""
    url: str = ""


class _VaultMatchIndex:
    def __init__(self) -> None:
        self.by_music_id: dict[str, _VaultMatchEntry] = {}
        self.by_url: dict[str, set[str]] = {}

    def add(self, data: dict[str, Any], *, folder: Path | None = None) -> None:
        urls = tuple(sorted(_metadata_url_variants(data)))
        music_id = _record_music_id(data)
        if not music_id:
            for url in urls:
                music_id = extract_music_id(url)
                if music_id:
                    break
        if not music_id and folder is not None:
            music_id = folder.name.split(" -", 1)[0].strip()
        if not music_id:
            return
        folder_text = _folder_text(data, folder)
        previous = self.by_music_id.get(music_id)
        if previous is not None:
            merged_urls = tuple(sorted({*previous.urls, *urls}))
            folder_text = previous.folder or folder_text
        else:
            merged_urls = urls
        self.by_music_id[music_id] = _VaultMatchEntry(
            music_id=music_id,
            folder=folder_text,
            urls=merged_urls,
        )
        for url in _url_variants_for_music_id(music_id) | set(merged_urls):
            self.by_url.setdefault(url, set()).add(music_id)


def annotate_vault_matches(records: list[FavoriteSoundRecord], vault_root: Path) -> list[FavoriteSoundRecord]:
    index = build_vault_match_index(vault_root)
    return [_record_with_vault_match(record, _match_record(record, index)) for record in records]


def build_vault_match_index(vault_root: Path) -> _VaultMatchIndex:
    index = _VaultMatchIndex()
    catalog_path = vault_root / "catalog" / "sounds.jsonl"
    if catalog_path.exists():
        try:
            with catalog_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(row, dict):
                        index.add(row)
        except OSError:
            pass
    sounds_root = vault_root / "sounds"
    if sounds_root.exists():
        try:
            folders = sorted(path for path in sounds_root.iterdir() if path.is_dir())
        except OSError:
            folders = []
        for folder in folders:
            metadata_path = folder / "metadata.json"
            data: dict[str, Any] = {}
            try:
                loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except (OSError, json.JSONDecodeError):
                data = {}
            index.add(data, folder=folder)
    return index


def _record_with_vault_match(record: FavoriteSoundRecord, match: _VaultMatch) -> FavoriteSoundRecord:
    return FavoriteSoundRecord(
        import_index=record.import_index,
        saved_at=record.saved_at,
        source_link=record.source_link,
        tiktok_music_id=record.tiktok_music_id,
        canonical_url_guess=record.canonical_url_guess,
        mobile_music_url=record.mobile_music_url,
        source=record.source,
        resolve_status=record.resolve_status,
        vault_match_status=match.status,
        vault_match_reason=match.reason,
        vault_match_music_id=match.music_id,
        vault_match_folder=match.folder,
        vault_match_url=match.url,
    )


def _match_record(record: FavoriteSoundRecord, index: _VaultMatchIndex) -> _VaultMatch:
    if record.tiktok_music_id and record.tiktok_music_id in index.by_music_id:
        entry = index.by_music_id[record.tiktok_music_id]
        return _VaultMatch(
            status="already_in_vault_by_music_id",
            reason="imported music ID matches an existing catalog or package record",
            music_id=entry.music_id,
            folder=entry.folder,
        )
    for status, url in (
        ("already_in_vault_by_canonical_url", record.canonical_url_guess),
        ("already_in_vault_by_mobile_url", record.mobile_music_url),
        ("already_in_vault_by_source_link", record.source_link),
    ):
        match = _match_url(url, index, status)
        if match is not None:
            return match
    return _VaultMatch(status="new_to_vault", reason="no matching music ID or TikTok URL found in vault")


def _match_url(url: str, index: _VaultMatchIndex, status: str) -> _VaultMatch | None:
    variants = normalized_tiktok_url_variants(url)
    music_ids: set[str] = set()
    for variant in variants:
        music_ids.update(index.by_url.get(variant, set()))
    if not music_ids:
        return None
    if len(music_ids) > 1:
        return _VaultMatch(
            status="ambiguous_url_match",
            reason=f"URL matched multiple existing music IDs: {', '.join(sorted(music_ids))}",
            url=next(iter(sorted(variants)), ""),
        )
    music_id = next(iter(music_ids))
    entry = index.by_music_id.get(music_id, _VaultMatchEntry(music_id=music_id))
    return _VaultMatch(
        status=status,
        reason="imported TikTok URL matches existing vault metadata",
        music_id=entry.music_id,
        folder=entry.folder,
        url=next(iter(sorted(variants)), ""),
    )


def normalized_tiktok_url_variants(url: str) -> set[str]:
    text = str(url or "").strip()
    if not text:
        return set()
    variants = set()
    normalized = _normalize_url(text)
    if normalized:
        variants.add(normalized)
    music_id = extract_music_id(text)
    if music_id:
        variants.update(_url_variants_for_music_id(music_id))
    return variants


def _url_variants_for_music_id(music_id: str) -> set[str]:
    if not music_id:
        return set()
    return {
        f"https://www.tiktok.com/music/-{music_id}",
        f"https://m.tiktok.com/h5/share/music/{music_id}.html",
        f"https://www.tiktok.com/music/{music_id}",
    }


def _normalize_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    parsed = urllib.parse.urlparse(text)
    if not parsed.scheme and parsed.path:
        parsed = urllib.parse.urlparse(f"https://{text}")
    if not parsed.netloc:
        return text.rstrip("/")
    netloc = parsed.netloc.lower()
    path = re.sub(r"/+", "/", parsed.path or "").rstrip("/")
    return urllib.parse.urlunparse(("https", netloc, path, "", "", ""))


def _record_music_id(data: dict[str, Any]) -> str:
    return str(data.get("tiktok_music_id") or data.get("music_id") or data.get("id") or "").strip()


def _folder_text(data: dict[str, Any], folder: Path | None) -> str:
    paths = data.get("paths")
    if isinstance(paths, dict) and paths.get("folder"):
        return str(paths["folder"])
    return str(folder) if folder is not None else ""


def _metadata_url_variants(data: dict[str, Any]) -> set[str]:
    urls = set()
    for url in _iter_url_values(data):
        urls.update(normalized_tiktok_url_variants(url))
    return urls


def _iter_url_values(value: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key).lower()
            if isinstance(child, str) and (
                "url" in key_text
                or "link" in key_text
                or "music" in key_text
                or "source" in key_text
            ):
                urls.append(child)
            elif isinstance(child, (dict, list, tuple)):
                urls.extend(_iter_url_values(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            urls.extend(_iter_url_values(child))
    return urls


def _favorite_sound_list(parsed: Any) -> list[Any]:
    if not isinstance(parsed, dict):
        raise ValueError("TikTok favorite sounds export must parse to an object")
    candidates = [
        parsed.get("Favorite Sounds"),
        parsed.get("FavoriteSounds"),
        parsed,
    ]
    for candidate in candidates:
        if isinstance(candidate, dict) and isinstance(candidate.get("FavoriteSoundList"), list):
            return list(candidate["FavoriteSoundList"])
    raise ValueError("TikTok favorite sounds export is missing FavoriteSoundList")


def _build_summary(
    *,
    input_path: Path,
    records: list[FavoriteSoundRecord],
    malformed_rows: int,
    outputs: dict[str, str],
) -> FavoriteSoundImportSummary:
    ids = [record.tiktok_music_id for record in records if record.tiktok_music_id]
    duplicate_count = len(ids) - len(set(ids))
    saved_dates = [record.saved_at for record in records if record.saved_at]
    vault_match_counts: dict[str, int] = {}
    for record in records:
        vault_match_counts[record.vault_match_status] = vault_match_counts.get(record.vault_match_status, 0) + 1
    by_year: dict[str, int] = {}
    for saved_at in saved_dates:
        year = saved_at[:4]
        if year.isdigit():
            by_year[year] = by_year.get(year, 0) + 1
    return FavoriteSoundImportSummary(
        source_file=str(input_path),
        source_size_bytes=input_path.stat().st_size,
        record_count=len(records),
        unique_music_ids=len(set(ids)),
        blank_ids=sum(1 for record in records if not record.tiktok_music_id),
        duplicate_music_ids=duplicate_count,
        malformed_rows=malformed_rows,
        already_in_vault=sum(1 for record in records if record.vault_match_status.startswith("already_in_vault")),
        new_to_vault=sum(1 for record in records if record.vault_match_status == "new_to_vault"),
        ambiguous_matches=sum(1 for record in records if record.vault_match_status == "ambiguous_url_match"),
        vault_match_counts=dict(sorted(vault_match_counts.items())),
        date_min=min(saved_dates) if saved_dates else "",
        date_max=max(saved_dates) if saved_dates else "",
        by_year=dict(sorted(by_year.items())),
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


def _write_csv_atomic(path: Path, records: list[FavoriteSoundRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    fields = list(FavoriteSoundRecord.__dataclass_fields__)
    with tmp_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow(asdict(record))
        handle.flush()
        os.fsync(handle.fileno())
    tmp_path.replace(path)
