from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

APP_DIR_NAME = "sound-vault"
LEGACY_APP_DIR_NAME = ".sound-vault"


def _mask_pair_code(pair_code: str) -> str:
    value = pair_code.strip().upper()
    if len(value) <= 4:
        return "…" + value[-2:]
    return f"{value[:4]}…{value[-4:]}"


def user_config_dir() -> Path:
    override = os.getenv("SOUND_VAULT_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        root = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(root) / "Sound Vault"
    if os.getenv("XDG_CONFIG_HOME"):
        return Path(os.environ["XDG_CONFIG_HOME"]) / APP_DIR_NAME
    if os.uname().sysname == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Sound Vault"
    return Path.home() / ".config" / APP_DIR_NAME


def user_data_dir() -> Path:
    override = os.getenv("SOUND_VAULT_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        root = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(root) / "Sound Vault"
    if os.getenv("XDG_DATA_HOME"):
        return Path(os.environ["XDG_DATA_HOME"]) / APP_DIR_NAME
    if os.uname().sysname == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Sound Vault"
    return Path.home() / ".local" / "share" / APP_DIR_NAME


def default_vault_root() -> Path:
    override = os.getenv("SOUND_VAULT_DEFAULT_VAULT")
    if override:
        return Path(override).expanduser()
    nas_default = Path("/nas/TikTok Sound Vault")
    if nas_default.exists():
        return nas_default
    return Path.home() / "Documents" / "Sound Vault"


def default_index_path() -> Path:
    return user_data_dir() / "index.sqlite3"


class AppSettings:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_config_dir() / "settings.json"
        self._data: dict[str, Any] = self._read()

    def vault_root(self) -> Path:
        value = self._data.get("vault_root")
        return Path(str(value)).expanduser() if value else default_vault_root()

    def set_vault_root(self, vault_root: Path) -> None:
        self._data["vault_root"] = str(vault_root.expanduser())
        self._write()

    def relay_base_url(self) -> str:
        return str(self._data.get("relay_base_url") or "")

    def relay_pair_code(self) -> str:
        return str(self._data.get("relay_pair_code") or "")

    def relay_device_id(self) -> str:
        return str(self._data.get("relay_device_id") or "")

    def relay_device_secret(self) -> str:
        return str(self._data.get("relay_device_secret") or "")

    def set_relay_config(
        self,
        *,
        base_url: str,
        pair_code: str,
        device_id: str = "",
        device_secret: str = "",
    ) -> None:
        self._data["relay_base_url"] = base_url.strip().rstrip("/")
        self._data["relay_pair_code"] = pair_code.strip().upper()
        if device_id:
            self._data["relay_device_id"] = device_id.strip()
        if device_secret:
            self._data["relay_device_secret"] = device_secret.strip()
        self._write()

    def relay_status_text(self) -> str:
        base_url = self.relay_base_url()
        pair_code = self.relay_pair_code()
        if not base_url:
            return "Relay not configured"
        if not pair_code:
            return f"Relay configured\n{base_url}\nPairing code needed"
        return f"Relay configured\n{base_url}\n{_mask_pair_code(pair_code)}"

    def table_layout(self, table_name: str) -> bytes | None:
        layouts = self._data.get("table_layouts")
        if not isinstance(layouts, dict):
            return None
        encoded = layouts.get(table_name)
        if not isinstance(encoded, str):
            return None
        try:
            return base64.b64decode(encoded.encode("ascii"), validate=True)
        except (ValueError, UnicodeEncodeError):
            return None

    def set_table_layout(self, table_name: str, header_state: bytes) -> None:
        layouts = self._data.setdefault("table_layouts", {})
        if not isinstance(layouts, dict):
            layouts = {}
            self._data["table_layouts"] = layouts
        layouts[table_name] = base64.b64encode(header_state).decode("ascii")
        self._write()

    def library_search_state(self) -> dict[str, Any]:
        state = self._data.get("library_search_state")
        return dict(state) if isinstance(state, dict) else {}

    def set_library_search_state(self, state: dict[str, Any]) -> None:
        allowed = {
            "query",
            "duration_filter",
            "media_filter",
            "status_filter",
            "usage_filter",
            "selected_music_id",
        }
        self._data["library_search_state"] = {
            key: str(value)
            for key, value in state.items()
            if key in allowed and value is not None
        }
        self._write()

    def hidden_table_columns(self, table_name: str) -> list[int]:
        tables = self._data.get("hidden_table_columns")
        if not isinstance(tables, dict):
            return []
        values = tables.get(table_name)
        if not isinstance(values, list):
            return []
        hidden: list[int] = []
        for value in values:
            try:
                hidden.append(int(value))
            except (TypeError, ValueError):
                continue
        return sorted(set(hidden))

    def set_hidden_table_columns(self, table_name: str, columns: list[int]) -> None:
        tables = self._data.setdefault("hidden_table_columns", {})
        if not isinstance(tables, dict):
            tables = {}
            self._data["hidden_table_columns"] = tables
        tables[table_name] = sorted(set(int(column) for column in columns if int(column) >= 0))
        self._write()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f".{self.path.name}.tmp")
        tmp_path.write_text(json.dumps(self._data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp_path, self.path)
        if os.name != "nt":
            os.chmod(self.path, 0o600)
