"""The app must open (and not lose the library view) when the vault drive — a
network mount in production — is unavailable."""
from __future__ import annotations

import json
import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_duplicate_decision_store_does_not_mkdir_on_init(tmp_path):
    # Constructing stores must never touch the filesystem — the path lives under
    # the (possibly offline) vault, and an eager mkdir there crashed app startup.
    from sound_vault.workers.dedupe_review import DuplicateDecisionStore

    target = tmp_path / "missing" / "reports" / "decisions.jsonl"
    store = DuplicateDecisionStore(target)  # must not raise, must not create dirs
    assert not target.parent.exists()
    # ...but the directory is created lazily on the first write.
    store.record_decision(group_id="g1", decision="keep")
    assert target.exists()
    assert store.read_decisions()[0]["group_id"] == "g1"


pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from sound_vault.settings import index_path_for_vault
from sound_vault.ui.view_model import LibraryViewModel
from sound_vault.ui.desktop import SoundVaultWindow


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_app_opens_and_keeps_cached_library_when_vault_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUND_VAULT_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("SOUND_VAULT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOUND_VAULT_DISABLE_RELAY_POLL", "1")
    monkeypatch.setenv("SOUND_VAULT_DISABLE_TIKTOK_PROMPT", "1")
    monkeypatch.setenv("SOUND_VAULT_DISABLE_TRANSCRIBE", "1")
    _app()

    vault = tmp_path / "vault"
    (vault / "sounds").mkdir(parents=True)
    for mid, title in [("111", "midnight drive"), ("222", "original sound")]:
        d = vault / "sounds" / f"{mid} - {title}"
        d.mkdir(parents=True)
        (d / f"{mid}.m4a").write_bytes(b"\x00" * 64)
        (d / "metadata.json").write_text(
            json.dumps(
                {
                    "tiktok_music_id": mid,
                    "tiktok_visible_title": title,
                    "tiktok_author_or_copyright": "Artist",
                    "status": "ingested",
                    "platform": "tiktok",
                    "paths": {"folder": f"sounds/{mid} - {title}", "audio": f"sounds/{mid} - {title}/{mid}.m4a"},
                }
            ),
            encoding="utf-8",
        )

    # Build the local index cache while the vault is online, then take it offline.
    vm = LibraryViewModel(
        vault_root=vault, index_path=index_path_for_vault(vault), load_sidecars=False, sidecar_mode="summary"
    )
    assert vm.rebuild_index() == 2
    vm.close()
    shutil.rmtree(vault)
    assert not vault.exists()

    # The app must still open (no crash) pointed at the now-missing vault...
    window = SoundVaultWindow(vault_root=vault)
    try:
        # ...show a clear offline state, not a silent "0 sounds"...
        assert window.index_status_text.text() == "VAULT OFFLINE"
        # ...and keep showing the last-known library from the local cache.
        assert window.table.rowCount() == 2
    finally:
        window.close()
