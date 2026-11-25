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


@pytest.mark.parametrize(
    "legacy",
    ["https://sound-vault-relay.vercel.app", "https://sound-vault-relay-git-main-x.vercel.app/", "https://vercel.app"],
)
def test_saved_legacy_vercel_relay_moves_to_current_relay(tmp_path, legacy):
    # A pairing made before the relay left Vercel saved that host; the Vercel project is
    # gone and its pairings did not carry over, so the URL moves to the current relay
    # and the dead credentials are dropped (the app then asks for a new pairing).
    path = tmp_path / "settings.json"
    path.write_text(
        '{"relay_base_url": "%s", "relay_pair_code": "OLD-1", "relay_device_id": "d",'
        ' "relay_device_secret": "s", "vault_root": "/v"}' % legacy,
        encoding="utf-8",
    )

    settings = AppSettings(path)

    assert settings.relay_base_url() == "https://api.soundcache.io"
    assert settings.relay_pair_code() == ""
    assert settings.relay_device_id() == ""
    assert settings.relay_device_secret() == ""
    assert str(settings.vault_root()) == "/v"
    # Persisted, so the next launch (and the Pair iPhone QR) reads the new host.
    assert '"https://api.soundcache.io"' in path.read_text(encoding="utf-8")
    assert "OLD-1" not in path.read_text(encoding="utf-8")


def test_current_and_self_hosted_relays_are_left_alone(tmp_path):
    for url in ("https://api.soundcache.io", "https://relay.example.com", "http://192.168.1.5:43117"):
        path = tmp_path / "settings.json"
        AppSettings(path).set_relay_config(base_url=url, pair_code="KEEP-1", device_id="d", device_secret="s")
        loaded = AppSettings(path)
        assert loaded.relay_base_url() == url
        assert loaded.relay_pair_code() == "KEEP-1"


def test_typing_a_legacy_relay_saves_the_current_one(tmp_path):
    settings = AppSettings(tmp_path / "settings.json")
    settings.set_relay_config(base_url=" https://sound-vault-relay.vercel.app/ ", pair_code="NEW-1")
    assert settings.relay_base_url() == "https://api.soundcache.io"


def test_lookalike_hosts_are_not_treated_as_legacy():
    from sound_vault.settings import is_legacy_relay_url

    assert not is_legacy_relay_url("https://vercel.app.evil.example")
    assert not is_legacy_relay_url("https://notvercel.app")
    assert not is_legacy_relay_url("")
