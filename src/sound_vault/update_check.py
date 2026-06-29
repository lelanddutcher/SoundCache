"""Lightweight update check.

Sound Cache ships as an unsigned Python launcher (a venv that pip-installs the
bundled wheel), so a signed-app updater like Sparkle doesn't fit. Instead the app
does a best-effort check against a small hosted manifest — ``soundcache.io/latest.json``
``{"version": "0.4.0", "url": "https://soundcache.io/#download", "notes": "..."}`` —
and, if a newer version is published, surfaces a non-blocking "update available" nudge
with a download link. Never blocks or breaks launch; a failed/absent check is a no-op.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable

DEFAULT_LATEST_URL = "https://soundcache.io/latest.json"
_DOWNLOAD_FALLBACK = "https://soundcache.io/#download"


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    url: str
    notes: str


def _parse_version(value: str) -> tuple[int, ...]:
    """Numeric-tuple form of a dotted version (leading 'v' + pre-release suffixes
    tolerated): '1.2.3' -> (1,2,3), 'v0.4.0-beta' -> (0,4,0)."""
    parts: list[int] = []
    for chunk in str(value or "").strip().lstrip("vV").split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def is_newer(latest: str, current: str) -> bool:
    a, b = _parse_version(latest), _parse_version(current)
    width = max(len(a), len(b))
    a = a + (0,) * (width - len(a))
    b = b + (0,) * (width - len(b))
    return a > b


def _default_fetch(url: str, *, timeout: float = 6.0) -> dict:
    import urllib.request

    from sound_vault.net import ssl_context

    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response:  # nosec B310 - fixed https manifest URL
        return json.loads(response.read().decode("utf-8"))


def check_for_update(
    current_version: str,
    *,
    url: str = DEFAULT_LATEST_URL,
    fetch: "Callable[[str], dict] | None" = None,
) -> UpdateInfo | None:
    """Return UpdateInfo when the hosted manifest advertises a newer version, else
    None. Best-effort: any network/parse error returns None (never raises)."""
    fetcher = fetch or _default_fetch
    try:
        data = fetcher(url)
    except Exception:  # noqa: BLE001 - update check is best-effort, never fatal
        return None
    if not isinstance(data, dict):
        return None
    latest = str(data.get("version") or "").strip()
    if not latest or not is_newer(latest, current_version):
        return None
    download = str(data.get("url") or "").strip()
    if not download.startswith("https://"):  # never hand a non-https/odd scheme to the OS
        download = _DOWNLOAD_FALLBACK
    return UpdateInfo(version=latest, url=download, notes=str(data.get("notes") or "")[:500])
