from __future__ import annotations

from datetime import datetime, timezone
import json
import traceback
from typing import Any

from sound_vault.settings import user_data_dir


def diagnostic_log_path():
    return user_data_dir() / "events.jsonl"


def write_event(event: str, **fields: Any) -> None:
    """Best-effort structured breadcrumbs for launch and indexing failures."""
    try:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **{key: _json_safe(value) for key, value in fields.items()},
        }
        path = diagnostic_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n")
    except Exception:
        # Diagnostics must never become the reason the app fails to open.
        return


def exception_fields(exc: BaseException) -> dict[str, str]:
    return {
        "error_type": type(exc).__name__,
        "error": str(exc),
        "traceback": "".join(traceback.format_exception(exc)),
    }


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)
