import json
import logging

from fastapi.testclient import TestClient

import sound_vault.relay.server as relay_server
from sound_vault.relay.inbox import InboxStore
from sound_vault.relay.pairing import PairingRegistry
from sound_vault.settings import AppSettings
from sound_vault.ui.view_model import LibraryViewModel
from sound_vault.vault.indexer import SoundRecord


def test_settings_round_trips_relay_configuration_without_losing_device_secret(tmp_path):
    settings = AppSettings(tmp_path / "settings.json")

    settings.set_relay_config(
        base_url="https://relay.example",
        pair_code="VAULT-1234",
        device_id="dev_1",
        device_secret="secret_1",
    )

    loaded = AppSettings(tmp_path / "settings.json")
    assert loaded.relay_base_url() == "https://relay.example"
    assert loaded.relay_pair_code() == "VAULT-1234"
    assert loaded.relay_device_id() == "dev_1"
    assert loaded.relay_device_secret() == "secret_1"
    assert "secret_1" not in loaded.relay_status_text()


def test_view_model_resolves_play_target_from_local_audio_path(tmp_path):
    audio = tmp_path / "preview.m4a"
    audio.write_bytes(b"fake")
    record = SoundRecord(
        music_id="123",
        title="sound",
        artist="artist",
        tags=(),
        status="approved",
        raw={"paths": {"audio": str(audio)}},
    )

    assert LibraryViewModel.play_target_for(record) == audio


def test_view_model_resolves_play_target_from_preview_url():
    record = SoundRecord(
        music_id="123",
        title="sound",
        artist="artist",
        tags=(),
        status="approved",
        raw={"preview_url": "https://cdn.example/preview.m4a"},
    )

    assert LibraryViewModel.play_target_for(record) == "https://cdn.example/preview.m4a"


def test_view_model_async_rebuild_runs_in_background_and_updates_index(tmp_path):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    catalog.mkdir(parents=True)
    (catalog / "sounds.jsonl").write_text(
        json.dumps({"music_id": "123", "title": "Async Sound", "status": "approved"}) + "\n",
        encoding="utf-8",
    )
    vm = LibraryViewModel(vault_root=vault, index_path=tmp_path / "index.sqlite3")

    future = vm.rebuild_index_async()

    assert future.result(timeout=5) == 1
    assert vm.stats_text() == "1 sounds • 1 approved"
    assert vm.search("async")[0].music_id == "123"


def test_relay_rate_limits_repeated_submit_attempts(monkeypatch):
    now = {"value": 1000.0}
    relay_server.pairings = PairingRegistry(now=lambda: now["value"], code_ttl_seconds=10)
    relay_server.inbox = InboxStore(now=lambda: now["value"])
    relay_server.rate_limiter.reset()
    monkeypatch.setenv("SOUND_VAULT_RELAY_RATE_LIMIT", "2")
    monkeypatch.setenv("SOUND_VAULT_RELAY_RATE_WINDOW_SECONDS", "60")
    client = TestClient(relay_server.app)
    pair = client.post("/v1/pairing/create", json={"device_name": "Studio Mac"}).json()

    statuses = [
        client.post(
            "/v1/inbox/submit",
            json={
                "pair_code": pair["pair_code"],
                "url": f"https://www.tiktok.com/t/{idx}/",
                "source": "ios_shortcut",
            },
        ).status_code
        for idx in range(3)
    ]

    assert statuses == [200, 200, 429]


def test_relay_logs_mask_pair_codes_and_device_secrets(caplog):
    caplog.set_level(logging.INFO, logger="sound_vault.relay.server")
    relay_server.log_relay_event(
        "poll",
        pair_code="VAULT-1234",
        device_id="dev_secretish",
        device_secret="super-secret-token",
        url="https://www.tiktok.com/t/abc/",
    )

    log_text = caplog.text
    assert "VAULT-1234" not in log_text
    assert "super-secret-token" not in log_text
    assert "VAUL…1234" in log_text
    assert "[REDACTED]" in log_text


def test_desktop_source_exposes_settings_pairing_async_and_play_controls():
    source = open("src/sound_vault/ui/desktop.py", encoding="utf-8").read()

    assert "SettingsDialog" in source
    assert "Create pairing code" in source
    assert "Save relay settings" in source
    assert "rebuild_index_async" in source
    assert "QTimer" in source
    assert "self.play_button" in source
    assert "play_selected_sound" in source
