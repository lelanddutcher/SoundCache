from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DuplicateReviewGroup:
    group_id: str
    score: float
    reason: str
    candidates: tuple[dict[str, Any], ...]


def _candidate_from_row(row: dict[str, Any]) -> dict[str, Any]:
    candidate = {
        "music_id": str(row.get("music_id") or ""),
        "title": str(row.get("title") or ""),
        "artist": str(row.get("artist") or row.get("creator") or ""),
        "folder": str(row.get("folder") or row.get("folder_path") or ""),
        "local_audio_path": str(row.get("local_audio_path") or row.get("audio_path") or ""),
        "artwork_path": str(row.get("artwork_path") or row.get("thumbnail_path") or ""),
        "transcript_excerpt": str(row.get("transcript_excerpt") or ""),
        "duration_seconds": row.get("duration_seconds"),
        "score": row.get("score"),
        "reason": str(row.get("reason") or ""),
    }
    return {key: value for key, value in candidate.items() if value not in (None, "")}


def _group_from_explicit(row: dict[str, Any], index: int) -> DuplicateReviewGroup | None:
    raw_candidates = row.get("candidates")
    if not isinstance(raw_candidates, list):
        return None
    candidates = tuple(_candidate_from_row(item) for item in raw_candidates if isinstance(item, dict))
    if len(candidates) < 2:
        return None
    group_id = str(row.get("group_key") or row.get("group_id") or f"group-{index}")
    return DuplicateReviewGroup(
        group_id=group_id,
        score=float(row.get("score") or 0.0),
        reason=str(row.get("reason") or "candidate similarity"),
        candidates=candidates,
    )


def load_duplicate_review_groups(report_path: Path) -> list[DuplicateReviewGroup]:
    """Load duplicate candidates from either grouped or flat audit report JSON."""
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(payload, dict):
        rows = payload.get("groups") or payload.get("candidates") or payload.get("rows") or []
    else:
        rows = payload
    if not isinstance(rows, list):
        return []

    explicit: list[DuplicateReviewGroup] = []
    flat_groups: dict[str, list[dict[str, Any]]] = {}
    flat_reasons: dict[str, str] = {}
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        group = _group_from_explicit(row, idx)
        if group is not None:
            explicit.append(group)
            continue
        group_key = str(row.get("group_key") or row.get("group_id") or "").strip()
        if not group_key:
            continue
        flat_groups.setdefault(group_key, []).append(row)
        flat_reasons.setdefault(group_key, str(row.get("reason") or "candidate similarity"))
    if explicit:
        return explicit
    groups = []
    for group_key, group_rows in sorted(flat_groups.items()):
        candidates = tuple(_candidate_from_row(row) for row in group_rows)
        if len(candidates) < 2:
            continue
        groups.append(
            DuplicateReviewGroup(
                group_id=group_key,
                score=0.0,
                reason=flat_reasons.get(group_key, "candidate similarity"),
                candidates=candidates,
            )
        )
    return groups


def append_manual_duplicate_group(
    report_path: Path,
    candidates: list[dict[str, Any]],
    *,
    reason: str = "Manual duplicate group from Library selection.",
) -> DuplicateReviewGroup:
    normalized = _unique_candidates(candidates)
    if len(normalized) < 2:
        raise ValueError("select at least two unique sounds to create a duplicate review group")
    timestamp = datetime.now(UTC).replace(microsecond=0)
    digest = hashlib.sha1(
        ("|".join(sorted(str(candidate.get("music_id") or "") for candidate in normalized)) + timestamp.isoformat()).encode(
            "utf-8"
        )
    ).hexdigest()[:10]
    group_id = f"manual-{timestamp.strftime('%Y%m%dT%H%M%SZ')}-{digest}"
    group = DuplicateReviewGroup(
        group_id=group_id,
        score=1.0,
        reason=reason,
        candidates=tuple({**candidate, "group_key": group_id, "reason": reason, "score": 1.0} for candidate in normalized),
    )
    row = {
        "group_key": group.group_id,
        "score": group.score,
        "reason": group.reason,
        "source": "manual_library_selection",
        "created_at": timestamp.isoformat().replace("+00:00", "Z"),
        "candidates": list(group.candidates),
    }
    payload = _append_group_row(report_path, row)
    _write_report_payload(report_path, payload)
    return group


def _unique_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        item = _candidate_from_row(candidate)
        music_id = str(item.get("music_id") or "").strip()
        if not music_id or music_id in seen:
            continue
        seen.add(music_id)
        normalized.append(item)
    return normalized


def _append_group_row(report_path: Path, row: dict[str, Any]) -> Any:
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [row]
    if isinstance(payload, list):
        payload.append(row)
        return payload
    if isinstance(payload, dict):
        for key in ("groups", "candidates", "rows"):
            values = payload.get(key)
            if isinstance(values, list):
                values.append(row)
                return payload
        payload["groups"] = [row]
        return payload
    return [row]


def _write_report_payload(report_path: Path, payload: Any) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_name(f".{report_path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp_path, report_path)


class DuplicateDecisionStore:
    def __init__(self, path: Path) -> None:
        # No eager mkdir: this path lives under the (possibly offline) vault mount,
        # and constructing the store must never touch the filesystem — an
        # unavailable vault would otherwise crash app startup. mkdir on write.
        self.path = path

    def record_decision(
        self,
        *,
        group_id: str,
        decision: str,
        keep_music_id: str = "",
        duplicate_music_ids: list[str] | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        row = {
            "decided_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "group_id": group_id,
            "decision": decision,
            "keep_music_id": keep_music_id,
            "duplicate_music_ids": duplicate_music_ids or [],
            "notes": notes,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def read_decisions(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        decisions = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    decisions.append(row)
        return decisions
