"""Postgres relay store integration tests.

Skipped unless SOUND_VAULT_TEST_DATABASE_URL points at a Postgres instance, so the
default suite stays DB-free. Run against a throwaway local Postgres (or Neon).
"""
import os

import pytest

DSN = os.getenv("SOUND_VAULT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="set SOUND_VAULT_TEST_DATABASE_URL to run")


def _clean_inbox(store):
    with store._connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM inbox_items")
        cur.execute("DELETE FROM pair_codes")
        cur.execute("DELETE FROM devices")
        conn.commit()


def test_pg_inbox_delivers_once():
    from sound_vault.relay.pg_store import PostgresInboxStore

    store = PostgresInboxStore(DSN, now=lambda: 1000.0)
    _clean_inbox(store)
    store.register_device(device_id="d1", device_secret="secret")
    store.register_pair_code("RIVER-7421", device_id="d1")
    item = store.submit_link(pair_code="RIVER-7421", url="https://x/y", source="ios_shortcut")
    assert store.poll(device_id="d1", device_secret="wrong", pair_code="RIVER-7421") == []
    delivered = store.poll(device_id="d1", device_secret="secret", pair_code="RIVER-7421")
    assert [d.url for d in delivered] == [item.url]
    assert store.poll(device_id="d1", device_secret="secret", pair_code="RIVER-7421") == []


def test_pg_secret_is_hashed_at_rest():
    from sound_vault.relay.pg_store import PostgresInboxStore

    store = PostgresInboxStore(DSN, now=lambda: 1000.0)
    _clean_inbox(store)
    store.register_device(device_id="d1", device_secret="plaintext-secret")
    with store._connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT device_secret_hash FROM devices WHERE device_id = 'd1'")
        stored = cur.fetchone()[0]
    assert stored != "plaintext-secret"


def test_pg_pair_code_ttl():
    from sound_vault.relay.pg_store import PostgresInboxStore

    clock = {"v": 1000.0}
    store = PostgresInboxStore(DSN, now=lambda: clock["v"], pair_code_ttl_seconds=10)
    with store._connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM pair_codes")
        conn.commit()
    store.register_pair_code("RIVER-2345", device_id="d1")
    assert store.can_accept_pair_code("river-2345") is True
    clock["v"] = 1011.0
    assert store.can_accept_pair_code("RIVER-2345") is False


def test_pg_leaderboard_ranks_and_windows():
    from sound_vault.relay.pg_store import PostgresLeaderboardStore

    store = PostgresLeaderboardStore(DSN, now=lambda: 1_000_000.0)
    store.reset()
    store.record_save(sound_id="a", title="Alpha", artist="X", platform="tiktok")
    store.record_save(sound_id="a")  # must not clobber title
    store.record_save(sound_id="b", title="Beta")
    store.record_save(sound_id="old", occurred_at=1_000_000.0 - 8 * 24 * 60 * 60)
    board = store.leaderboard()
    by = [(e.sound_id, e.saves) for e in board]
    assert by[0] == ("a", 2)
    assert board[0].title == "Alpha"
    week = [e.sound_id for e in store.leaderboard(window="7d")]
    assert "old" not in week and "a" in week
