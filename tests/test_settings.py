import os

import pytest

from sound_vault.settings import (
    AppSettings,
    default_index_path,
    default_vault_root,
    index_path_for_vault,
    user_config_dir,
    user_data_dir,
)


def test_default_vault_can_be_overridden_by_env(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    monkeypatch.setenv("SOUND_VAULT_DEFAULT_VAULT", str(vault))

    assert default_vault_root() == vault


def test_settings_round_trips_vault_root(tmp_path):
    settings = AppSettings(tmp_path / "settings.json")
    vault = tmp_path / "Sound Vault"

    settings.set_vault_root(vault)

    assert AppSettings(tmp_path / "settings.json").vault_root() == vault


def test_relay_base_url_defaults_to_public_relay(tmp_path):
    settings = AppSettings(tmp_path / "settings.json")
    # New users get a working relay so pairing is one click, no URL hunting.
    assert settings.relay_base_url() == "https://api.soundcache.io"
    settings.set_relay_config(base_url="https://my.relay", pair_code="ABC-1")
    assert AppSettings(tmp_path / "settings.json").relay_base_url() == "https://my.relay"


def test_config_and_data_dirs_can_be_overridden(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    monkeypatch.setenv("SOUND_VAULT_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SOUND_VAULT_DATA_DIR", str(data_dir))

    assert user_config_dir() == config_dir
    assert user_data_dir() == data_dir
    assert default_index_path() == data_dir / "index.sqlite3"


def test_index_path_is_scoped_to_selected_vault(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("SOUND_VAULT_DATA_DIR", str(data_dir))
    first = tmp_path / "Copied TikTok Sound Vault"
    second = tmp_path / "Other TikTok Sound Vault"

    first_path = index_path_for_vault(first)
    second_path = index_path_for_vault(second)

    assert first_path.parent == data_dir / "indexes"
    assert first_path.suffix == ".sqlite3"
    assert first_path != second_path


def test_settings_round_trips_table_header_layout_bytes(tmp_path):
    settings = AppSettings(tmp_path / "settings.json")
    header_state = b"qt-header-state"

    settings.set_table_layout("library", header_state)

    assert AppSettings(tmp_path / "settings.json").table_layout("library") == header_state
    assert AppSettings(tmp_path / "settings.json").table_layout("missing") is None


def test_settings_round_trips_library_search_state(tmp_path):
    settings = AppSettings(tmp_path / "settings.json")
    state = {
        "query": "money printer",
        "duration_filter": "under_30",
        "media_filter": "has_audio",
        "status_filter": "needs_review",
        "library_filter": "favorites",
        "selected_music_id": "123",
    }

    settings.set_library_search_state(state)

    assert AppSettings(tmp_path / "settings.json").library_search_state() == state


def test_relay_settings_file_is_owner_read_write_only_on_posix(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX file modes are not enforced on Windows")
    path = tmp_path / "settings.json"

    AppSettings(path).set_relay_config(
        base_url="https://relay.example.test",
        pair_code="abcd-1234",
        device_id="device-1",
        device_secret="secret-token",
    )

    assert path.stat().st_mode & 0o777 == 0o600


def test_settings_ignores_corrupt_table_header_layout(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"table_layouts": {"library": "not-base64!!"}}\n', encoding="utf-8")

    assert AppSettings(path).table_layout("library") is None
