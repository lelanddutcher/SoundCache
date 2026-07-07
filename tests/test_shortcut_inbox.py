from sound_vault.ingest.shortcut_inbox import ShortcutInboxStore


def test_shortcut_inbox_appends_unique_pending_links(tmp_path):
    store = ShortcutInboxStore(tmp_path / "shortcut-inbox.jsonl")

    first = store.add_url("https://www.tiktok.com/t/abc/", source="ios_shortcut", relay_id="in_1")
    duplicate = store.add_url("https://www.tiktok.com/t/abc/", source="ios_shortcut", relay_id="in_1")

    assert first.id == duplicate.id
    assert len(store.pending()) == 1
    assert store.pending()[0].url == "https://www.tiktok.com/t/abc/"


def test_shortcut_inbox_can_mark_item_imported(tmp_path):
    store = ShortcutInboxStore(tmp_path / "shortcut-inbox.jsonl")
    item = store.add_url("https://www.tiktok.com/t/abc/", source="ios_shortcut", relay_id="in_1")

    store.mark_imported(item.id)

    assert store.pending() == []
    assert store.all_items()[0].status == "imported"


def test_failed_items_stay_in_queue_and_can_be_requeued(tmp_path):
    store = ShortcutInboxStore(tmp_path / "shortcut-inbox.jsonl")
    item = store.add_url("https://www.tiktok.com/t/abc/", source="ios_shortcut", relay_id="in_1")

    # exhaust attempts -> the item becomes 'failed' but is NOT dropped from the queue
    store.record_failure(item.id, "boom", max_attempts=2)
    store.record_failure(item.id, "boom again", max_attempts=2)
    assert store.pending() == []
    failed = store.failed()
    assert len(failed) == 1 and failed[0].id == item.id
    assert failed[0].status == "failed"
    assert failed[0].error == "boom again"  # error is retained for reporting

    # retry: back to pending, attempts + error cleared, so the next import re-attempts it
    assert store.requeue(item.id) is True
    assert store.failed() == []
    again = store.pending()
    assert len(again) == 1 and again[0].id == item.id
    assert again[0].attempts == 0
    assert again[0].error is None


def test_requeue_all_failed_bulk_resets(tmp_path):
    store = ShortcutInboxStore(tmp_path / "shortcut-inbox.jsonl")
    a = store.add_url("https://www.tiktok.com/t/a/", source="ios_shortcut", relay_id="in_a")
    b = store.add_url("https://www.tiktok.com/t/b/", source="ios_shortcut", relay_id="in_b")
    store.mark_failed(a.id, "err a")
    store.mark_failed(b.id, "err b")
    assert len(store.failed()) == 2

    assert store.requeue_all_failed() == 2
    assert store.failed() == []
    assert len(store.pending()) == 2
