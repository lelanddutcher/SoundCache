from sound_vault.relay.server import build_inbox_store


def test_build_inbox_store_uses_sqlite_path_from_env(monkeypatch, tmp_path):
    db_path = tmp_path / "relay.sqlite3"
    monkeypatch.setenv("SOUND_VAULT_RELAY_STORAGE_PATH", str(db_path))

    store = build_inbox_store()
    store.register_pair_code("RIVER-7421", device_id="dev_1")

    restarted = build_inbox_store()

    assert db_path.exists()
    assert restarted.can_accept_pair_code("RIVER-7421") is True
