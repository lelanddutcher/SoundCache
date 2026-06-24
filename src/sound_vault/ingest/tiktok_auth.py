"""Manage the TikTok browser session Sound Cache uses to capture sound audio.

TikTok only serves a sound's playable audio to a *logged-in* browser session, so
the capture (``scripts/capture_tiktok_audio.cjs``) drives Chromium with a saved
Playwright ``storageState``. This module owns where that state lives, how to tell
whether it is still active, and how to (re)connect it via an interactive login.

The user's credentials never pass through here: they sign in on TikTok's own
login page in a real browser window; only the resulting session cookies are
saved, and only on this machine (file-private, in the app data dir).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sound_vault.settings import user_data_dir

# Cookies that mark a logged-in (active) TikTok session. sessionid is the real
# auth cookie; sid_guard carries the long-lived expiry.
_SESSION_COOKIES = ("sessionid", "sessionid_ss", "sid_guard")


def state_path() -> Path:
    """Canonical path to the saved TikTok session (overridable for power users)."""
    override = os.getenv("SOUND_VAULT_TIKTOK_STATE")
    if override:
        return Path(override).expanduser()
    return user_data_dir() / "tiktok.storageState.json"


def _repo_root() -> Path:
    # src/sound_vault/ingest/tiktok_auth.py -> parents[3] is the repo root
    # (holds scripts/ + node_modules/, so `require('playwright')` resolves).
    return Path(__file__).resolve().parents[3]


def login_script() -> Path:
    return _repo_root() / "scripts" / "tiktok_login.cjs"


def project_cwd() -> Path:
    return _repo_root()


def login_command(*, out_path: Path | None = None) -> list[str]:
    """argv for the interactive login (opens a real TikTok login window)."""
    return ["node", str(login_script()), str(out_path or state_path())]


@dataclass(frozen=True)
class TikTokAuthStatus:
    connected: bool
    expires_at: datetime | None
    days_left: int | None
    reason: str  # short, human-readable

    @property
    def headline(self) -> str:
        if not self.connected:
            return "TikTok not connected"
        if self.days_left is not None:
            return f"TikTok connected · session active ({self.days_left} days left)"
        return "TikTok connected · session active"

    @property
    def expiring_soon(self) -> bool:
        return self.connected and self.days_left is not None and self.days_left <= 7


def connection_status(path: Path | None = None, *, now: datetime | None = None) -> TikTokAuthStatus:
    """Inspect the saved session: connected? when does it expire?

    "Connected" means a usable session cookie exists and hasn't expired. Reads
    only the local file — no network — so it's cheap to call on every refresh.
    """
    path = path or state_path()
    now = now or datetime.now(timezone.utc)
    if not path.exists():
        return TikTokAuthStatus(False, None, None, "No TikTok login saved yet.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return TikTokAuthStatus(False, None, None, "Saved login is unreadable — reconnect.")
    cookies = data.get("cookies") if isinstance(data, dict) else None
    if not isinstance(cookies, list):
        return TikTokAuthStatus(False, None, None, "Saved login has no cookies — reconnect.")
    session = [
        c for c in cookies
        if isinstance(c, dict) and c.get("name") in _SESSION_COOKIES and c.get("value")
    ]
    if not session:
        return TikTokAuthStatus(False, None, None, "Saved login is missing the TikTok session — reconnect.")
    # Earliest positive expiry across the session cookies (<=0 == session cookie).
    expiries = [
        float(c["expires"]) for c in session
        if isinstance(c.get("expires"), (int, float)) and c["expires"] > 0
    ]
    if not expiries:
        # Session cookies present but no expiry recorded — treat as active.
        return TikTokAuthStatus(True, None, None, "TikTok session active.")
    expires_at = datetime.fromtimestamp(min(expiries), tz=timezone.utc)
    if expires_at <= now:
        return TikTokAuthStatus(False, expires_at, 0, "TikTok session expired — reconnect.")
    days_left = max(0, (expires_at - now).days)
    return TikTokAuthStatus(True, expires_at, days_left, "TikTok session active.")


def is_valid_state_file(path: Path) -> bool:
    """True when ``path`` looks like a usable TikTok storageState (has a session)."""
    return connection_status(path).connected


def harden_state_file(path: Path | None = None) -> None:
    """Ensure the saved session is private (0600 file, 0700 parent) no matter
    which code path last wrote it. Session cookies are account-takeover-grade."""
    path = path or state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.exists():
            os.chmod(path, 0o600)
    except OSError:
        pass


def disconnect(path: Path | None = None) -> bool:
    """Delete the saved session. Returns True if a file was removed."""
    path = path or state_path()
    try:
        path.unlink()
        return True
    except (FileNotFoundError, OSError):
        return False
