from sound_vault.relay.inbox import InboxStore


def test_device_secret_never_stored_in_plaintext_in_memory():
    store = InboxStore(now=lambda: 1000.0)
    store.register_device(device_id="d1", device_secret="super-secret-token")
    assert "super-secret-token" not in store._devices.values()
    assert store._devices["d1"] != "super-secret-token"


def test_device_secret_never_written_plaintext_to_disk(tmp_path):
    db_path = tmp_path / "relay.sqlite3"
    store = InboxStore(now=lambda: 1000.0, db_path=db_path)
    store.register_device(device_id="d1", device_secret="super-secret-token")
    assert b"super-secret-token" not in db_path.read_bytes()


def test_poll_still_authenticates_after_hashing(tmp_path):
    db_path = tmp_path / "relay.sqlite3"
    store = InboxStore(now=lambda: 1000.0, db_path=db_path)
    store.register_device(device_id="d1", device_secret="secret")
    store.register_pair_code("RIVER-7421", device_id="d1")
    store.submit_link(pair_code="RIVER-7421", url="https://x/y", source="ios_shortcut")

    assert store.poll(device_id="d1", device_secret="wrong", pair_code="RIVER-7421") == []
    delivered = store.poll(device_id="d1", device_secret="secret", pair_code="RIVER-7421")
    assert [d.url for d in delivered] == ["https://x/y"]


def test_hashed_secret_survives_restart(tmp_path):
    db_path = tmp_path / "relay.sqlite3"
    first = InboxStore(now=lambda: 1000.0, db_path=db_path)
    first.register_device(device_id="d1", device_secret="secret")
    first.register_pair_code("RIVER-7421", device_id="d1")
    first.submit_link(pair_code="RIVER-7421", url="https://x/y", source="ios_shortcut")

    restarted = InboxStore(now=lambda: 1000.0, db_path=db_path)
    delivered = restarted.poll(device_id="d1", device_secret="secret", pair_code="RIVER-7421")
    assert [d.url for d in delivered] == ["https://x/y"]
