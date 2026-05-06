from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Callable
import urllib.parse
import urllib.request


@dataclass(frozen=True)
class RelayInboxItem:
    id: str
    url: str
    source: str


def _default_get_json(url: str, *, params: dict[str, str], headers: dict[str, str], timeout: float) -> dict[str, Any]:
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(full_url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - user-configured relay URL
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
        return [RelayInboxItem(id=str(item["id"]), url=str(item["url"]), source=str(item.get("source", "unknown"))) for item in payload.get("items", [])]

    def poll_to_inbox(self, inbox_path: Path) -> list[RelayInboxItem]:
        items = self.poll()
        if not items:
            return []
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        with inbox_path.open("a", encoding="utf-8") as handle:
            for item in items:
                handle.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")
        return items
