from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any

_USAGE_RE = re.compile(r"(?P<number>\d+(?:\.\d+)?)\s*(?P<suffix>[KMB])?\s+videos?\b", re.IGNORECASE)
_MULTIPLIERS = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


@dataclass(frozen=True)
class UsageBackfillResult:
    music_id: str
    usage_count: int | None
    usage_count_label: str
    source: str
    captured_at: str
    ok: bool
    error: str = ""


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_usage_count_label(text: str) -> tuple[int | None, str]:
    """Parse TikTok labels like '214 videos', '475.7K videos', or '6.9M videos'."""
    match = _USAGE_RE.search(str(text or ""))
    if not match:
        return None, ""
    suffix = (match.group("suffix") or "").upper()
    value = float(match.group("number")) * _MULTIPLIERS[suffix]
    label = match.group(0).strip()
    return int(round(value)), label


def extract_usage_from_text_candidates(candidates: list[str]) -> tuple[int | None, str, str]:
    for source, text in enumerate(candidates):
        usage_count, label = parse_usage_count_label(text)
        if usage_count is not None:
            return usage_count, label, f"text_candidate_{source}"
    return None, "", ""


def update_metadata_usage_count(folder: Path, result: UsageBackfillResult) -> None:
    metadata_path = folder / "metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read metadata for {folder}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ValueError(f"metadata is not an object: {metadata_path}")

    if result.usage_count is not None:
        metadata["usage_count"] = result.usage_count
        metadata["usage_count_label"] = result.usage_count_label
        metadata["usage_count_source"] = result.source
        metadata["usage_count_captured_at"] = result.captured_at
    evidence = metadata.setdefault("evidence", {})
    if isinstance(evidence, dict):
        evidence["usage_count_backfill"] = asdict(result)
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sound_folders_missing_usage(vault_root: Path) -> list[Path]:
    sounds_root = vault_root / "sounds"
    folders = []
    for metadata_path in sorted(sounds_root.glob("*/metadata.json")):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            folders.append(metadata_path.parent)
            continue
        if not isinstance(metadata, dict) or metadata.get("usage_count") in (None, ""):
            folders.append(metadata_path.parent)
    return folders


def music_url_for_folder(folder: Path) -> tuple[str, str]:
    metadata_path = folder / "metadata.json"
    metadata: dict[str, Any] = {}
    try:
        loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            metadata = loaded
    except (OSError, json.JSONDecodeError):
        pass
    music_id = str(metadata.get("tiktok_music_id") or folder.name.split(" -", 1)[0]).strip()
    url = str(metadata.get("canonical_url") or metadata.get("mobile_music_url") or "").strip()
    if not url and music_id:
        url = f"https://www.tiktok.com/music/-{music_id}"
    return music_id, url
