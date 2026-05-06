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
