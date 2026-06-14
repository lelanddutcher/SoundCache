#!/usr/bin/env python3
"""Audit likely duplicate Sound Vault sounds without deleting anything."""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Sequence

from sound_vault.vault.indexer import SoundRecord, build_index

VAULT_ROOT = Path("/nas/TikTok Sound Vault")

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "so",
    "the",
    "to",
    "we",
    "you",
    "your",
}


@dataclass(frozen=True)
class DuplicateCandidate:
    group_key: str
    music_id: str
    title: str
    artist: str
    duration_seconds: float | None
    folder: str
    local_audio_path: str
    artwork_path: str
    transcript_excerpt: str
    score: float
    reason: str


@dataclass(frozen=True)
class DuplicateCandidateGroup:
    group_key: str
    score: float
    reason: str
    candidates: tuple[DuplicateCandidate, ...]


@dataclass(frozen=True)
class PairEvidence:
    left: SoundRecord
    right: SoundRecord
    score: float
    reasons: tuple[str, ...]


def normalize_text(value: Any) -> str:
    text = str(value or "").lower().strip()
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value: Any) -> set[str]:
    return {
        token
        for token in normalize_text(value).split()
        if token and token not in _STOPWORDS and len(token) > 1
    }


def _text_similarity(left: str, right: str) -> float:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0
    left_tokens = _tokens(left_norm)
    right_tokens = _tokens(right_norm)
    token_score = 0.0
    if left_tokens and right_tokens:
        token_score = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    sequence_score = SequenceMatcher(None, left_norm[:1200], right_norm[:1200]).ratio()
    return max(token_score, sequence_score)


def _meaningful_transcript(value: str) -> bool:
    return len(_tokens(value)) >= 5 or len(normalize_text(value)) >= 40


def _duration_closeness(left: float | None, right: float | None) -> tuple[float, str | None]:
    if left is None or right is None:
        return 0.0, None
    diff = abs(left - right)
    larger = max(left, right, 1.0)
    ratio = diff / larger
    if diff <= 1.5 or ratio <= 0.05:
        return 0.18, f"duration close ({left:.1f}s vs {right:.1f}s)"
    if diff <= 3.0 and ratio <= 0.15:
        return 0.10, f"duration near ({left:.1f}s vs {right:.1f}s)"
    return -1.0, f"duration mismatch ({left:.1f}s vs {right:.1f}s)"


def _visual_path(record: SoundRecord) -> Path | None:
    if record.artwork_path is not None and record.artwork_path.exists():
        return record.artwork_path
    for image in record.evidence_images:
        if image.exists():
            return image
    return None


def _file_fingerprint(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        stat = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return f"{stat.st_size}:{digest.hexdigest()}"
    except OSError:
        return ""


def _record_group_key(records: Sequence[SoundRecord]) -> str:
    seed = sorted(records, key=lambda record: record.music_id)[0]
    return f"{normalize_text(seed.title)}|{normalize_text(seed.artist)}"


def _candidate_from_record(record: SoundRecord, *, group_key: str, score: float, reason: str) -> DuplicateCandidate:
    transcript = " ".join(record.transcript_text.split())
    return DuplicateCandidate(
        group_key=group_key,
        music_id=record.music_id,
        title=record.title,
        artist=record.artist,
        duration_seconds=record.duration_seconds,
        folder=str(record.folder_path or ""),
        local_audio_path=str(record.local_audio_path or ""),
        artwork_path=str(_visual_path(record) or ""),
        transcript_excerpt=transcript[:180],
        score=round(score, 3),
        reason=reason,
    )


def _pair_evidence(left: SoundRecord, right: SoundRecord) -> PairEvidence | None:
    title_similarity = _text_similarity(left.title, right.title)
    artist_similarity = _text_similarity(left.artist, right.artist)
    if title_similarity < 0.90 or artist_similarity < 0.78:
        return None

    score = 0.0
    reasons: list[str] = []
    blockers: list[str] = []

    if title_similarity >= 0.98:
        score += 0.35
        reasons.append("same normalized title")
    else:
        score += 0.25
        reasons.append(f"similar title ({title_similarity:.2f})")

    if artist_similarity >= 0.98:
        score += 0.25
        reasons.append("same normalized artist/source")
    else:
        score += 0.18
        reasons.append(f"similar artist/source ({artist_similarity:.2f})")

    duration_score, duration_reason = _duration_closeness(left.duration_seconds, right.duration_seconds)
    if duration_score < 0:
        blockers.append(duration_reason or "duration mismatch")
    elif duration_reason:
        score += duration_score
        reasons.append(duration_reason)

    left_transcript = left.transcript_text or ""
    right_transcript = right.transcript_text or ""
    left_has_transcript = _meaningful_transcript(left_transcript)
    right_has_transcript = _meaningful_transcript(right_transcript)
    if left_has_transcript and right_has_transcript:
        transcript_similarity = _text_similarity(left_transcript, right_transcript)
        if transcript_similarity < 0.45:
            blockers.append(f"transcripts differ ({transcript_similarity:.2f})")
        elif transcript_similarity >= 0.72:
            score += 0.30
            reasons.append(f"transcripts close ({transcript_similarity:.2f})")
        elif transcript_similarity >= 0.55:
            score += 0.18
            reasons.append(f"transcripts somewhat close ({transcript_similarity:.2f})")
        else:
            blockers.append(f"transcripts weak ({transcript_similarity:.2f})")

    left_visual = _file_fingerprint(_visual_path(left))
    right_visual = _file_fingerprint(_visual_path(right))
    if left_visual and right_visual:
        if left_visual == right_visual:
            score += 0.18
            reasons.append("same artwork/thumbnail fingerprint")
        else:
            score -= 0.12
            reasons.append("different artwork/thumbnail fingerprints")

    if left.local_audio_path and right.local_audio_path and left.local_audio_path == right.local_audio_path:
        score += 0.35
        reasons.append("same local audio path")

    if left.canonical_url and right.canonical_url and left.canonical_url == right.canonical_url:
        score += 0.30
        reasons.append("same canonical URL")

    if blockers:
        return None

    corroborated = any(
        phrase in reason
        for reason in reasons
        for phrase in (
            "duration close",
            "duration near",
            "transcripts close",
            "transcripts somewhat close",
            "same artwork",
            "same local audio",
            "same canonical",
        )
    )
    if score < 0.72 or not corroborated:
        return None
    return PairEvidence(left=left, right=right, score=min(score, 1.0), reasons=tuple(reasons))


def _candidate_pairs(records: Sequence[SoundRecord]) -> list[PairEvidence]:
    by_artist: dict[str, list[SoundRecord]] = {}
    for record in records:
        artist_key = normalize_text(record.artist)
        title_key = normalize_text(record.title)
        if not record.music_id or not artist_key or not title_key:
            continue
        by_artist.setdefault(artist_key, []).append(record)

    pairs: list[PairEvidence] = []
    for artist_records in by_artist.values():
        if len(artist_records) < 2:
            continue
        sorted_records = sorted(artist_records, key=lambda record: (normalize_text(record.title), record.music_id))
        for left_idx, left in enumerate(sorted_records):
            for right in sorted_records[left_idx + 1 :]:
                evidence = _pair_evidence(left, right)
                if evidence is not None:
                    pairs.append(evidence)
    return pairs


def _connected_groups(pairs: Sequence[PairEvidence]) -> list[DuplicateCandidateGroup]:
    if not pairs:
        return []
    records_by_id: dict[str, SoundRecord] = {}
    adjacency: dict[str, set[str]] = {}
    pair_reasons: dict[frozenset[str], PairEvidence] = {}
    for pair in pairs:
        left_id = pair.left.music_id
        right_id = pair.right.music_id
        records_by_id[left_id] = pair.left
        records_by_id[right_id] = pair.right
        adjacency.setdefault(left_id, set()).add(right_id)
        adjacency.setdefault(right_id, set()).add(left_id)
        pair_reasons[frozenset((left_id, right_id))] = pair

    groups: list[DuplicateCandidateGroup] = []
    seen: set[str] = set()
    for music_id in sorted(adjacency):
        if music_id in seen:
            continue
        stack = [music_id]
        component: set[str] = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(adjacency.get(current, set()) - component)
        seen.update(component)
        if len(component) < 2:
            continue
        component_records = tuple(records_by_id[item] for item in sorted(component))
        component_pairs = [
            pair
            for key, pair in pair_reasons.items()
            if key.issubset(component)
        ]
        score = max(pair.score for pair in component_pairs)
        reason_parts: list[str] = []
        for pair in sorted(component_pairs, key=lambda item: item.score, reverse=True)[:3]:
            reason_parts.append(f"{pair.left.music_id}<->{pair.right.music_id}: {', '.join(pair.reasons)}")
        reason = " | ".join(reason_parts)
        group_key = _record_group_key(component_records)
        candidates = tuple(
            _candidate_from_record(record, group_key=group_key, score=score, reason=reason)
            for record in component_records
        )
        groups.append(DuplicateCandidateGroup(group_key=group_key, score=round(score, 3), reason=reason, candidates=candidates))
    return sorted(groups, key=lambda group: (-group.score, group.group_key))


def find_duplicate_groups(vault_root: Path) -> list[DuplicateCandidateGroup]:
    records = build_index(vault_root, load_sidecars=True, sidecar_mode="summary")
    return _connected_groups(_candidate_pairs(records))


def find_duplicate_candidates(vault_root: Path) -> list[DuplicateCandidate]:
    return [candidate for group in find_duplicate_groups(vault_root) for candidate in group.candidates]


def _coerce_groups(items: Sequence[DuplicateCandidate | DuplicateCandidateGroup]) -> list[DuplicateCandidateGroup]:
    if not items:
        return []
    first = items[0]
    if isinstance(first, DuplicateCandidateGroup):
        return list(items)  # type: ignore[arg-type]
    grouped: dict[str, list[DuplicateCandidate]] = {}
    for item in items:
        if isinstance(item, DuplicateCandidate):
            grouped.setdefault(item.group_key, []).append(item)
    groups: list[DuplicateCandidateGroup] = []
    for group_key, candidates in sorted(grouped.items()):
        score = max(candidate.score for candidate in candidates)
        reason = candidates[0].reason if candidates else ""
        groups.append(DuplicateCandidateGroup(group_key=group_key, score=score, reason=reason, candidates=tuple(candidates)))
    return groups


def write_outputs(
    candidates_or_groups: Sequence[DuplicateCandidate | DuplicateCandidateGroup],
    out_dir: Path,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "duplicate-candidates.json"
    csv_path = out_dir / "duplicate-candidates.csv"
    groups = _coerce_groups(candidates_or_groups)
    payload = [
        {
            "group_key": group.group_key,
            "score": group.score,
            "reason": group.reason,
            "candidates": [candidate.__dict__ for candidate in group.candidates],
        }
        for group in groups
    ]
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "group_key",
            "group_score",
            "music_id",
            "title",
            "artist",
            "duration_seconds",
            "folder",
            "local_audio_path",
            "artwork_path",
            "transcript_excerpt",
            "score",
            "reason",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for group in groups:
            for candidate in group.candidates:
                row = candidate.__dict__ | {"group_score": group.score}
                writer.writerow(row)
    return json_path, csv_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit likely duplicate Sound Vault records; does not delete files.")
    parser.add_argument("--vault", type=Path, default=VAULT_ROOT)
    parser.add_argument("--out", type=Path, default=VAULT_ROOT / "reports")
    args = parser.parse_args()
    groups = find_duplicate_groups(args.vault)
    json_path, csv_path = write_outputs(groups, args.out)
    candidate_count = sum(len(group.candidates) for group in groups)
    print(f"duplicate candidate rows: {candidate_count:,}")
    print(f"duplicate groups: {len(groups):,}")
    print(json_path)
    print(csv_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
