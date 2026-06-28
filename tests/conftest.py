"""Keep the test suite hermetic: point app-data (index default, the shortcut
inbox, events log, saved TikTok session) at a throwaway per-session directory so
tests never read from — or write into — the real ~/Library app-data, and stay
deterministic across runs."""
from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_app_data(tmp_path_factory):
    base = tmp_path_factory.mktemp("sound-cache-appdata")
    # setdefault so an explicit override in the environment still wins.
    os.environ.setdefault("SOUND_VAULT_DATA_DIR", str(base / "data"))
    os.environ.setdefault("SOUND_VAULT_CONFIG_DIR", str(base / "config"))
    yield
