"""Client-side opt-in save-event reporter for the global leaderboard.

Posts ONLY anonymized data — sound id + platform + title/artist. No device
secret, no account, no file paths, no personal identifiers. Reporting is best
effort and must never block or fail ingest.
"""
from __future__ import annotations

import json
import ssl
import urllib.request
from typing import Callable


def _ssl_context() -> ssl.SSLContext | None:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return None


def _default_post(url: str, payload: dict, *, timeout: float = 8.0) -> int:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:  # nosec B310
        return getattr(response, "status", 0)


class SaveEventReporter:
    def __init__(
        self,
        *,
        base_url: str,
        enabled: bool = True,
        post: Callable[..., int] = _default_post,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.enabled = enabled
        self._post = post

    def report_save(self, *, sound_id: str, platform: str = "", title: str = "", artist: str = "") -> bool:
        if not self.enabled or not self.base_url or not sound_id:
            return False
        payload = {
            "sound_id": sound_id,
            "platform": platform or "",
            "title": title or "",
            "artist": artist or "",
        }
        try:
            self._post(f"{self.base_url}/v1/events/save", payload)
            return True
        except Exception:  # noqa: BLE001 - telemetry must never break ingest
            return False
