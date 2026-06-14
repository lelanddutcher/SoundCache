from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sound_vault.ingest.shortcut_inbox import ShortcutInboxStore


def test_concurrent_add_url_does_not_lose_items(tmp_path):
    """Regression: the auto-poll worker, a manual import, and the launchd agent
    can call add_url() at once. Without the exclusive lock the read-modify-write
    silently dropped items."""
    store = ShortcutInboxStore(tmp_path / "inbox.jsonl")
    urls = [f"https://example.com/sound/{i}" for i in range(60)]

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda u: store.add_url(u, source="t"), urls))

    saved = {item.url for item in store.all_items()}
    assert saved == set(urls), f"lost {len(set(urls) - saved)} items to a write race"


def test_concurrent_add_is_idempotent_on_same_url(tmp_path):
    store = ShortcutInboxStore(tmp_path / "inbox.jsonl")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _i: store.add_url("https://example.com/dupe", source="t"), range(16)))
    assert len(store.all_items()) == 1


def test_record_failure_increments_atomically(tmp_path):
    store = ShortcutInboxStore(tmp_path / "inbox.jsonl")
    item = store.add_url("https://example.com/x", source="t")
    store.record_failure(item.id, "boom", max_attempts=3)
    store.record_failure(item.id, "boom", max_attempts=3)
    [reread] = store.all_items()
    assert reread.attempts == 2
    assert reread.status == "pending"
    store.record_failure(item.id, "boom", max_attempts=3)
    [reread] = store.all_items()
    assert reread.attempts == 3
    assert reread.status == "failed"
