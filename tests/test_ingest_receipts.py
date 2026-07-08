import json

from sound_vault.ingest.receipts import ReceiptLedger
from sound_vault.ingest.service import IngestService
from sound_vault.ingest.shortcut_inbox import ShortcutInboxStore
from sound_vault.relay.client import RelayClient, RelayInboxItem

from test_ingest_service import FakeDownloader, make_service, tiktok_music


# ---- ReceiptLedger unit ---------------------------------------------------


def test_ledger_append_and_read_roundtrip(tmp_path):
    ledger = ReceiptLedger(tmp_path / "receipts.jsonl")
    ledger.record_received_many(
        [{"relay_id": "in_1", "url": "https://t/a", "source": "ios", "note": "hi"}]
    )
    ledger.record_imported(relay_id="in_1", url="https://t/a", music_id="A", folder="sounds/A - x")
    ledger.record_failed(relay_id="in_2", url="https://t/b", error="boom", terminal=True)

    events = ledger.read_events()
    assert [e.event for e in events] == ["received", "imported", "failed"]
    assert events[0].relay_id == "in_1" and events[0].note == "hi"
    assert events[1].music_id == "A" and events[1].folder == "sounds/A - x"
    assert events[2].terminal is True


def test_ledger_is_append_only_never_rewritten(tmp_path):
    path = tmp_path / "receipts.jsonl"
    ledger = ReceiptLedger(path)
    ledger.record_received_many([{"relay_id": "in_1", "url": "https://t/a"}])
    ledger.record_received_many([{"relay_id": "in_2", "url": "https://t/b"}])
    # Two separate appends => two lines preserved (history is never collapsed).
    assert len([ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]) == 2


def test_ledger_skips_unparseable_lines(tmp_path):
    path = tmp_path / "receipts.jsonl"
    path.write_text(
        '{"event":"received","relay_id":"in_1","url":"https://t/a"}\n'
        "this is not json\n"
        '{"event":"imported","relay_id":"in_1","url":"https://t/a","music_id":"A"}\n',
        encoding="utf-8",
    )
    events = ReceiptLedger(path).read_events()
    assert [e.event for e in events] == ["received", "imported"]


def test_ledger_deliveries_and_latest_outcome(tmp_path):
    ledger = ReceiptLedger(tmp_path / "receipts.jsonl")
    ledger.record_received_many([{"relay_id": "in_1", "url": "https://t/a"}])
    ledger.record_received_many([{"relay_id": "in_2", "url": "https://t/b"}])
    ledger.record_failed(relay_id="in_1", url="https://t/a", error="e1")
    ledger.record_imported(relay_id="in_1", url="https://t/a", music_id="A", folder="sounds/A")

    deliveries = ledger.deliveries()
    assert set(deliveries) == {"in_1", "in_2"}
    outcome = ledger.latest_outcome()
    assert outcome["in_1"].event == "imported"  # latest wins over the earlier failure
    assert "in_2" not in outcome  # never had a terminal outcome


# ---- poll_to_inbox writes the receipt BEFORE the queue --------------------


def _client(items):
    def fake_get(url, *, params, headers, timeout):
        return {"items": items}

    return RelayClient(
        base_url="https://relay.example", device_id="dev_1", device_secret="s",
        pair_code="RIVER-7421", get_json=fake_get,
    )


def test_poll_to_inbox_records_receipts_before_queueing(tmp_path):
    inbox = tmp_path / "inbox" / "shortcut-inbox.jsonl"
    client = _client([
        {"id": "in_1", "url": "https://www.tiktok.com/t/a/", "source": "ios_shortcut", "note": "n1"},
        {"id": "in_2", "url": "https://www.tiktok.com/t/b/", "source": "ios_shortcut"},
    ])
    items = client.poll_to_inbox(inbox)
    assert items == [
        RelayInboxItem(id="in_1", url="https://www.tiktok.com/t/a/", source="ios_shortcut", note="n1"),
        RelayInboxItem(id="in_2", url="https://www.tiktok.com/t/b/", source="ios_shortcut"),
    ]
    # The durable receipt ledger sits beside the inbox and holds every delivery.
    receipts = ReceiptLedger.beside(inbox).deliveries()
    assert set(receipts) == {"in_1", "in_2"}
    assert receipts["in_1"].url == "https://www.tiktok.com/t/a/"
    # And the queue got both, via a single bulk write.
    assert {i.relay_id for i in ShortcutInboxStore(inbox).pending()} == {"in_1", "in_2"}


def test_poll_twice_dedups_queue_but_keeps_full_receipt_history(tmp_path):
    inbox = tmp_path / "inbox" / "shortcut-inbox.jsonl"
    client = _client([{"id": "in_1", "url": "https://www.tiktok.com/t/a/", "source": "ios_shortcut"}])
    client.poll_to_inbox(inbox)
    client.poll_to_inbox(inbox)
    # Queue deduped on relay_id (still one item)...
    assert len(ShortcutInboxStore(inbox).all_items()) == 1
    # ...but the append-only receipt ledger recorded both deliveries (audit trail).
    received = [e for e in ReceiptLedger.beside(inbox).read_events() if e.event == "received"]
    assert len(received) == 2


# ---- drain_inbox stamps music_id + records the ledger outcome -------------


def test_drain_inbox_stamps_music_id_and_records_imported(tmp_path):
    store = ShortcutInboxStore(tmp_path / "inbox.jsonl")
    store.add_url("https://www.tiktok.com/t/a/", source="ios_shortcut", relay_id="in_1")
    resolve_map = {"https://www.tiktok.com/t/a/": tiktok_music("https://www.tiktok.com/t/a/", music_id="A")}
    svc = make_service(tmp_path, FakeDownloader(), resolve_map=resolve_map)

    svc.drain_inbox(store)

    item = store.all_items()[0]
    assert item.status == "imported"
    assert item.music_id == "A"  # resolved id persisted on the queue row
    outcome = ReceiptLedger.beside(store.path).latest_outcome()
    assert outcome["in_1"].event == "imported" and outcome["in_1"].music_id == "A"
    assert outcome["in_1"].folder  # the vault folder was recorded for reconciliation


def test_drain_inbox_records_failed_outcome_to_ledger(tmp_path):
    store = ShortcutInboxStore(tmp_path / "inbox.jsonl")
    store.add_url("https://www.tiktok.com/t/x/", source="ios_shortcut", relay_id="in_1")
    svc = make_service(tmp_path, FakeDownloader(ok=False, error="boom"))

    svc.drain_inbox(store, max_attempts=1)  # terminal on first failure

    outcome = ReceiptLedger.beside(store.path).latest_outcome()
    assert outcome["in_1"].event == "failed"
    assert outcome["in_1"].terminal is True
    assert "boom" in (outcome["in_1"].error or "")
