import sqlite3

from sound_vault.relay.inbox import InboxStore


def test_sqlite_inbox_store_survives_restart_for_submit_and_poll(tmp_path):
    db_path = tmp_path / "relay.sqlite3"
    first = InboxStore(db_path=db_path, now=lambda: 1000.0)
    first.register_device(device_id="dev_1", device_secret="secret")
    first.register_pair_code("RIVER-7421", device_id="dev_1")
    first.submit_link(pair_code="RIVER-7421", url="https://www.tiktok.com/t/abc/", source="ios_shortcut")

    restarted = InboxStore(db_path=db_path, now=lambda: 1001.0)
    delivered = restarted.poll(device_id="dev_1", device_secret="secret", pair_code="RIVER-7421")

    assert [item.url for item in delivered] == ["https://www.tiktok.com/t/abc/"]
    assert restarted.poll(device_id="dev_1", device_secret="secret", pair_code="RIVER-7421") == []


def test_sqlite_inbox_note_survives_restart(tmp_path):
    db_path = tmp_path / "relay.sqlite3"
    first = InboxStore(db_path=db_path, now=lambda: 1000.0)
    first.register_device(device_id="dev_1", device_secret="secret")
    first.register_pair_code("RIVER-7421", device_id="dev_1")
    first.submit_link(
        pair_code="RIVER-7421", url="https://www.tiktok.com/t/abc/", source="ios_shortcut", note="wedding vibes"
    )

    restarted = InboxStore(db_path=db_path, now=lambda: 1001.0)
    [delivered] = restarted.poll(device_id="dev_1", device_secret="secret", pair_code="RIVER-7421")
    assert delivered.note == "wedding vibes"


def test_sqlite_inbox_migrates_legacy_schema_without_note_column(tmp_path):
    db_path = tmp_path / "relay.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE inbox_items (
                id TEXT PRIMARY KEY,
                pair_code_hash TEXT NOT NULL,
                url TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )

    # Opening the store should add the note column rather than crash.
    store = InboxStore(db_path=db_path, now=lambda: 1000.0)
    store.register_device(device_id="dev_1", device_secret="secret")
    store.register_pair_code("RIVER-7421", device_id="dev_1")
    store.submit_link(pair_code="RIVER-7421", url="https://x/", source="ios_shortcut", note="hi")
    [delivered] = store.poll(device_id="dev_1", device_secret="secret", pair_code="RIVER-7421")
    assert delivered.note == "hi"


def test_sqlite_pair_code_acceptance_survives_restart(tmp_path):
    db_path = tmp_path / "relay.sqlite3"
    first = InboxStore(db_path=db_path, now=lambda: 1000.0, pair_code_ttl_seconds=30)
    first.register_pair_code("RIVER-7421", device_id="dev_1")

    restarted = InboxStore(db_path=db_path, now=lambda: 1001.0, pair_code_ttl_seconds=30)

    assert restarted.can_accept_pair_code("RIVER-7421") is True


def test_sqlite_poll_deletes_expired_items_without_restart(tmp_path):
    now = {"value": 1000.0}
    db_path = tmp_path / "relay.sqlite3"
    store = InboxStore(db_path=db_path, now=lambda: now["value"], item_ttl_seconds=10)
    store.register_device(device_id="dev_1", device_secret="secret")
    store.register_pair_code("RIVER-7421", device_id="dev_1")
    store.submit_link(pair_code="RIVER-7421", url="https://www.tiktok.com/t/abc/", source="ios_shortcut")
    now["value"] = 1011.0

    assert store.poll(device_id="dev_1", device_secret="secret", pair_code="RIVER-7421") == []
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM inbox_items").fetchone()[0] == 0
