from __future__ import annotations

import re
from typing import Any

HASHTAG_RE = re.compile(r"(?<!/)#([^\s#]+)", re.UNICODE)
_TRAILING_PUNCTUATION = ".,;:!?)]}>'\"`’”"


def normalize_hashtag(value: Any) -> str:
    tag = str(value or "").strip()
    if tag.startswith("#"):
        tag = tag[1:]
    tag = tag.strip().strip(_TRAILING_PUNCTUATION).strip()
    if not tag or "://" in tag:
        return ""
    return tag.casefold()


def unique_hashtags(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        tag = normalize_hashtag(value)
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
    return tuple(out)


def extract_hashtags_from_text(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    text = str(value)
    return unique_hashtags([match.group(1) for match in HASHTAG_RE.finditer(text)])


def _iter_text_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        texts: list[str] = []
        for nested in value.values():
            texts.extend(_iter_text_values(nested))
        return texts
    if isinstance(value, (list, tuple, set)):
        texts: list[str] = []
        for nested in value:
            texts.extend(_iter_text_values(nested))
        return texts
    return []


def extract_hashtags(*values: Any) -> tuple[str, ...]:
    tags: list[str] = []
    for value in values:
        for text in _iter_text_values(value):
            tags.extend(extract_hashtags_from_text(text))
    return unique_hashtags(tags)


def extract_hashtags_from_video_record(record: dict[str, Any]) -> tuple[str, ...]:
    return unique_hashtags(
        [
            *_coerce_hashtag_list(record.get("hashtags")),
            *extract_hashtags(
                record.get("description"),
                record.get("page_title"),
                record.get("video_title"),
                record.get("music_page_card"),
                record.get("body"),
                record.get("metaDesc"),
                record.get("ogDesc"),
                record.get("ogTitle"),
            ),
        ]
    )


def enrich_video_record_hashtags(record: dict[str, Any]) -> dict[str, Any]:
    hashtags = extract_hashtags_from_video_record(record)
    if not hashtags:
        return dict(record)
    enriched = dict(record)
    enriched["hashtags"] = list(hashtags)
    return enriched


def aggregate_video_hashtags(records: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> tuple[str, ...]:
    tags: list[str] = []
    for record in records:
        if isinstance(record, dict):
            tags.extend(extract_hashtags_from_video_record(record))
    return unique_hashtags(tags)


def _coerce_hashtag_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return unique_hashtags([value])
    if isinstance(value, (list, tuple, set)):
        return unique_hashtags([str(item) for item in value])
    return ()
