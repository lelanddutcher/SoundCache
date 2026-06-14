from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable
import urllib.parse
import urllib.request

from sound_vault.ingest.shortcut_inbox import ShortcutInboxStore
from sound_vault.net import ssl_context


@dataclass(frozen=True)
class RelayInboxItem:
    id: str
    url: str
    source: str
    note: str = ""


def _default_get_json(url: str, *, params: dict[str, str], headers: dict[str, str], timeout: float) -> dict[str, Any]:
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(full_url, headers=headers, method="GET")
    # certifi CA bundle: framework Python has none, so plain HTTPS fails with
    # CERTIFICATE_VERIFY_FAILED — which silently broke relay polling.
    with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response:  # nosec B310 - user-configured relay URL
        return json.loads(response.read().decode("utf-8"))


class RelayClient:
    def __init__(
        self,
        *,
        base_url: str,
        device_id: str,
        device_secret: str,
        pair_code: str,
        get_json: Callable[..., dict[str, Any]] = _default_get_json,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.device_id = device_id
        self.device_secret = device_secret
        self.pair_code = pair_code
        self._get_json = get_json

    def poll(self) -> list[RelayInboxItem]:
        payload = self._get_json(
            f"{self.base_url}/v1/inbox/poll",
            params={"pair_code": self.pair_code},
            headers={"x-device-id": self.device_id, "x-device-secret": self.device_secret},
            timeout=20.0,
        )
        items = []
        for item in payload.get("items", []):
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "").strip()
            url = str(item.get("url") or "").strip()
            if not item_id or not url:
                continue
            items.append(
                RelayInboxItem(
                    id=item_id,
                    url=url,
                    source=str(item.get("source") or "unknown"),
                    note=str(item.get("note") or ""),
                )
            )
        return items

    def poll_to_inbox(self, inbox_path: Path) -> list[RelayInboxItem]:
        items = self.poll()
        if not items:
            return []
        store = ShortcutInboxStore(inbox_path)
        for item in items:
            store.add_url(item.url, source=item.source, relay_id=item.id, note=item.note)
        return items
