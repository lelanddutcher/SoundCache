import json

from sound_vault.db.index_db import IndexDatabase
from sound_vault.ingest.shortcut_inbox import ShortcutInboxStore
from sound_vault.relay.client import RelayClient, RelayInboxItem
from sound_vault.vault.indexer import build_index


def _write_catalog(vault, lines):
    catalog = vault / "catalog"
    catalog.mkdir(parents=True)
    (catalog / "sounds.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_catalog_skips_malformed_rows_and_keeps_valid_records(tmp_path):
    vault = tmp_path / "vault"
    _write_catalog(
        vault,
        [
            json.dumps({"music_id": "m1", "title": "First"}),
            "not json",
            json.dumps({"music_id": "m2", "title": "Second"}),
        ],
    )

    records = build_index(vault)

    assert [record.music_id for record in records] == ["m1", "m2"]


def test_catalog_normalizes_bad_tag_and_video_count_types(tmp_path):
    vault = tmp_path / "vault"
    _write_catalog(
        vault,
        [
            json.dumps(
                {
                    "music_id": "m1",
                    "title": "Hype Track",
                    "tags": "hype",
                    "associated_video_count": "not-an-int",
                }
            )
        ],
    )

    [record] = build_index(vault)

    assert record.tags == ("hype",)
    assert record.associated_video_count == 0
    assert "m1" in record.search_text


def test_index_rebuild_dedupes_duplicate_music_ids_last_record_wins(tmp_path):
    vault = tmp_path / "vault"
    _write_catalog(
        vault,
        [
            json.dumps({"music_id": "dup", "title": "Old", "tags": ["one"]}),
            json.dumps({"music_id": "dup", "title": "New", "tags": ["two"]}),
        ],
    )
    records = build_index(vault)
    db = IndexDatabase(tmp_path / "index.sqlite3")

    db.rebuild(records)

    results = db.search("dup")
    assert len(results) == 1
    assert results[0].title == "New"
    assert results[0].tags == ("two",)


def test_index_search_limit_is_clamped(tmp_path):
    db = IndexDatabase(tmp_path / "index.sqlite3")
    db.rebuild([])

    assert db.search("", limit=-1) == []


def test_inbox_skips_bad_rows_ignores_unknown_fields_and_dedupes_existing_file(tmp_path):
    path = tmp_path / "shortcut-inbox.jsonl"
    valid = {
        "id": "url_1",
        "url": "https://www.tiktok.com/t/abc/",
        "source": "ios_shortcut",
        "status": "pending",
        "created_at": "2026-01-01T00:00:00+00:00",
        "relay_id": "in_1",
        "extra": "ignored",
    }
    duplicate = {**valid, "id": "url_2"}
    legacy = {"id": "in_legacy", "url": "https://www.tiktok.com/t/legacy/", "source": "relay"}
    path.write_text(
        "not json\n"
        + json.dumps(valid)
        + "\n"
        + json.dumps(duplicate)
        + "\n"
        + json.dumps(legacy)
        + "\n",
        encoding="utf-8",
    )

    pending = ShortcutInboxStore(path).pending()

    assert [item.url for item in pending] == [
        "https://www.tiktok.com/t/abc/",
        "https://www.tiktok.com/t/legacy/",
    ]
    assert pending[1].status == "pending"


def test_relay_client_skips_malformed_items_and_dedupes_writes_to_shortcut_inbox(tmp_path):
    calls = []

    def fake_get(url, *, params, headers, timeout):
        calls.append(1)
        return {
            "items": [
                {"id": "bad_missing_url"},
                {"url": "https://www.tiktok.com/t/missing-id/"},
                {"id": "in_1", "url": "https://www.tiktok.com/t/abc/", "source": "ios_shortcut"},
            ]
        }

    client = RelayClient(
        base_url="https://relay.example",
        device_id="dev_1",
        device_secret="secret",
        pair_code="RIVER-7421",
        get_json=fake_get,
    )
    inbox_path = tmp_path / "shortcut-inbox.jsonl"

    first = client.poll_to_inbox(inbox_path)
    second = client.poll_to_inbox(inbox_path)

    assert first == [RelayInboxItem(id="in_1", url="https://www.tiktok.com/t/abc/", source="ios_shortcut")]
    assert second == [RelayInboxItem(id="in_1", url="https://www.tiktok.com/t/abc/", source="ios_shortcut")]
    assert [item.url for item in ShortcutInboxStore(inbox_path).pending()] == ["https://www.tiktok.com/t/abc/"]
