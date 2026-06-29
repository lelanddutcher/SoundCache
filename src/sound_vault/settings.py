from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

APP_DIR_NAME = "sound-vault"
LEGACY_APP_DIR_NAME = ".sound-vault"

# Human-readable folder name used for macOS/Windows app dirs and the default
# vault. The product was renamed Sound Vault -> Sound Cache; we prefer the new
# name but transparently fall back to an existing legacy folder so renaming the
# app never orphans a user's saved settings, index, or vault.
APP_DISPLAY_NAME = "Sound Cache"
LEGACY_DISPLAY_NAME = "Sound Vault"

# Public relay used out-of-the-box so a new user can pair without hunting for a
# URL. Self-hosters can still override it in Settings. (Same backend as the
# legacy sound-vault-relay.vercel.app host.)
DEFAULT_RELAY_BASE_URL = "https://api.soundcache.io"


def _named_app_dir(parent: Path) -> Path:
    """Return ``parent / "Sound Cache"``, but keep using an existing legacy
    ``parent / "Sound Vault"`` folder if the new one hasn't been created yet."""
    new_dir = parent / APP_DISPLAY_NAME
    legacy_dir = parent / LEGACY_DISPLAY_NAME
    if not new_dir.exists() and legacy_dir.exists():
        return legacy_dir
    return new_dir


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
        return _named_app_dir(Path(root))
    if os.getenv("XDG_CONFIG_HOME"):
        return Path(os.environ["XDG_CONFIG_HOME"]) / APP_DIR_NAME
    if os.uname().sysname == "Darwin":
        return _named_app_dir(Path.home() / "Library" / "Application Support")
    return Path.home() / ".config" / APP_DIR_NAME


def user_data_dir() -> Path:
    override = os.getenv("SOUND_VAULT_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        root = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return _named_app_dir(Path(root))
    if os.getenv("XDG_DATA_HOME"):
        return Path(os.environ["XDG_DATA_HOME"]) / APP_DIR_NAME
    if os.uname().sysname == "Darwin":
        return _named_app_dir(Path.home() / "Library" / "Application Support")
    return Path.home() / ".local" / "share" / APP_DIR_NAME


def default_vault_root() -> Path:
    override = os.getenv("SOUND_VAULT_DEFAULT_VAULT")
    if override:
        return Path(override).expanduser()
    return _named_app_dir(Path.home() / "Documents")


def default_index_path() -> Path:
    return user_data_dir() / "index.sqlite3"


def _vault_digest(vault_root: Path) -> str:
    normalized = str(vault_root.expanduser())
    return hashlib.sha256(normalized.encode("utf-8", errors="surrogatepass")).hexdigest()[:16]


def index_path_for_vault(vault_root: Path) -> Path:
    """Return a SQLite cache path scoped to the selected vault root."""
    return user_data_dir() / "indexes" / f"{_vault_digest(vault_root)}.sqlite3"


def inbox_path_for_vault(vault_root: Path) -> Path:
    """Return the shortcut-inbox queue path scoped to the vault root.

    Lives in LOCAL app-data (not under the vault mount) so polling/queuing is
    reliable even when the network-mounted vault is offline; keyed to the vault so
    distinct vaults don't share a queue."""
    return user_data_dir() / "inbox" / f"{_vault_digest(vault_root)}.jsonl"


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

    def vault_root_is_set(self) -> bool:
        """True once a vault has been explicitly chosen (vs. the implicit default).
        Used to tell a brand-new install apart from a returning user."""
        return bool(self._data.get("vault_root"))

    def recent_vaults(self) -> list[str]:
        values = self._data.get("recent_vaults")
        if not isinstance(values, list):
            return []
        return [str(v) for v in values if isinstance(v, str) and v.strip()]

    def add_recent_vault(self, vault_root: Path, *, limit: int = 8) -> None:
        path = str(Path(vault_root).expanduser())
        recents = [p for p in self.recent_vaults() if p != path]
        recents.insert(0, path)
        self._data["recent_vaults"] = recents[:limit]
        self._write()

    def onboarding_complete(self) -> bool:
        return bool(self._data.get("onboarding_complete"))

    def set_onboarding_complete(self, complete: bool = True) -> None:
        self._data["onboarding_complete"] = bool(complete)
        self._write()

    def relay_base_url(self) -> str:
        return str(self._data.get("relay_base_url") or DEFAULT_RELAY_BASE_URL)

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

    def telemetry_enabled(self) -> bool:
        # Opt-out: anonymized save events feed the global leaderboard, ON by default.
        value = self._data.get("telemetry_enabled")
        return True if value is None else bool(value)

    def set_telemetry_enabled(self, enabled: bool) -> None:
        self._data["telemetry_enabled"] = bool(enabled)
        self._write()

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
            "library_filter",
            "selected_music_id",
        }
        self._data["library_search_state"] = {
            key: str(value)
            for key, value in state.items()
            if key in allowed and value is not None
        }
        self._write()

    def transcription_config(self) -> dict[str, Any]:
        defaults = {
            "preferred_provider": "cloud",
            "cloud_provider": "openai",
            "cloud_base_url": "https://api.openai.com/v1",
            "cloud_model": "gpt-4o-transcribe",
            "local_engine": "faster-whisper",
            "local_model": "base",
            "model_cache_dir": "",
            "demucs_enabled": False,
            "demucs_model": "htdemucs_ft",
        }
        value = self._data.get("transcription")
        if isinstance(value, dict):
            defaults.update({key: value for key, value in value.items() if key in defaults})
        return defaults

    def set_transcription_config(
        self,
        *,
        preferred_provider: str,
        cloud_provider: str,
        cloud_base_url: str,
        cloud_model: str,
        local_engine: str,
        local_model: str,
        model_cache_dir: str = "",
        demucs_enabled: bool = False,
        demucs_model: str = "htdemucs_ft",
    ) -> None:
        self._data["transcription"] = {
            "preferred_provider": preferred_provider if preferred_provider in {"cloud", "local"} else "cloud",
            "cloud_provider": cloud_provider.strip() or "openai",
            "cloud_base_url": cloud_base_url.strip().rstrip("/") or "https://api.openai.com/v1",
            "cloud_model": cloud_model.strip() or "gpt-4o-transcribe",
            "local_engine": local_engine.strip() or "faster-whisper",
            "local_model": local_model.strip() or "base",
            "model_cache_dir": model_cache_dir.strip(),
            "demucs_enabled": bool(demucs_enabled),
            "demucs_model": demucs_model.strip() or "htdemucs_ft",
        }
        self._write()

    def capture_config(self) -> dict[str, Any]:
        defaults = {
            "aggressiveness": "metadata_only",
            "require_manual_login": True,
            "max_batch_items": 25,
            "delay_seconds": 10,
            "stop_on_checkpoint": True,
        }
        value = self._data.get("capture")
        if isinstance(value, dict):
            defaults.update({key: value for key, value in value.items() if key in defaults})
        return defaults

    def set_capture_config(
        self,
        *,
        aggressiveness: str,
        require_manual_login: bool = True,
        max_batch_items: int = 25,
        delay_seconds: int = 10,
        stop_on_checkpoint: bool = True,
    ) -> None:
        allowed = {"metadata_only", "artwork", "preview_audio", "full_audio", "associated_videos"}
        self._data["capture"] = {
            "aggressiveness": aggressiveness if aggressiveness in allowed else "metadata_only",
            "require_manual_login": bool(require_manual_login),
            "max_batch_items": max(1, int(max_batch_items)),
            "delay_seconds": max(0, int(delay_seconds)),
            "stop_on_checkpoint": bool(stop_on_checkpoint),
        }
        self._write()

    def set_secret_reference(self, name: str, reference: str) -> None:
        secrets = self._data.setdefault("secret_references", {})
        if not isinstance(secrets, dict):
            secrets = {}
            self._data["secret_references"] = secrets
        # Store only keyring/env references, never raw pasted credentials.
        if reference.startswith(("keyring:", "env:")):
            secrets[name] = reference
        else:
            secrets[name] = ""
        self._write()

    def secret_reference(self, name: str) -> str:
        secrets = self._data.get("secret_references")
        if not isinstance(secrets, dict):
            return ""
        value = secrets.get(name)
        return str(value) if isinstance(value, str) else ""

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
