"""End-to-end coverage for the User Notes feature.

A note can ride in from the iOS Shortcut (relay -> inbox -> ingest -> metadata.json)
or be typed in the app's inspector. Either way it lands in the sound's
metadata.json (file-native truth), mirrors into the index, and becomes
searchable through the same FTS query as everything else.
"""
from __future__ import annotations

import json

from sound_vault.db.index_db import IndexDatabase
from sound_vault.ingest.shortcut_inbox import ShortcutInboxStore
from sound_vault.relay.client import RelayClient
from sound_vault.ui.view_model import LibraryViewModel
from sound_vault.vault.indexer import SoundRecord


def _record(music_id: str, **kw) -> SoundRecord:
    base = dict(music_id=music_id, title="T", artist="A", tags=(), status="approved", raw={})
    base.update(kw)
    return SoundRecord(**base)


# --- index layer ----------------------------------------------------------


def test_index_db_round_trips_and_searches_user_notes(tmp_path):
    db = IndexDatabase(tmp_path / "index.sqlite3")
    db.rebuild([_record("m1", user_notes="use for gym montage intros")])

    [record] = db.search("m1")
    assert record.user_notes == "use for gym montage intros"
    # notes feed FTS, so they're findable by the same search box
    assert [r.music_id for r in db.search("montage")] == ["m1"]


def test_update_user_notes_persists_and_reindexes(tmp_path):
    db_path = tmp_path / "index.sqlite3"
    db = IndexDatabase(db_path)
    db.rebuild([_record("m1")])
    assert db.search("warmup") == []

    assert db.update_user_notes("m1", "perfect warmup energy") is True
    assert db.get("m1").user_notes == "perfect warmup energy"
    assert [r.music_id for r in db.search("warmup")] == ["m1"]

    # survives reopen (column is real, not just in-memory)
    assert IndexDatabase(db_path).get("m1").user_notes == "perfect warmup energy"


def test_update_user_notes_unknown_id_returns_false(tmp_path):
    db = IndexDatabase(tmp_path / "index.sqlite3")
    db.rebuild([_record("m1")])
    assert db.update_user_notes("nope", "x") is False


# --- view model (file-native truth) ---------------------------------------


def _seed_vault_with_sound(tmp_path, music_id="123"):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    sound_dir = vault / "sounds" / f"{music_id} - Seed"
    catalog.mkdir(parents=True)
    sound_dir.mkdir(parents=True)
    (catalog / "sounds.jsonl").write_text(
        json.dumps(
            {
                "tiktok_music_id": music_id,
                "tiktok_visible_title": "Seed Sound",
                "paths": {"folder": str(sound_dir)},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (sound_dir / "metadata.json").write_text(
        json.dumps({"tiktok_music_id": music_id}), encoding="utf-8"
    )
    return vault, sound_dir


def test_set_user_notes_writes_metadata_json_and_makes_searchable(tmp_path):
    vault, sound_dir = _seed_vault_with_sound(tmp_path)
    vm = LibraryViewModel(vault_root=vault, index_path=tmp_path / "index.sqlite3")
    vm.rebuild_index()

    assert vm.set_user_notes("123", "save for cozy autumn edits") is True

    # file-native truth updated
    meta = json.loads((sound_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["user_notes"] == "save for cozy autumn edits"

    # index updated + searchable through the normal search path
    assert [row.music_id for row in vm.search("autumn")] == ["123"]
    assert vm.preview_for("123").user_notes == "save for cozy autumn edits"


def test_set_user_notes_unknown_id_returns_false(tmp_path):
    vault, _ = _seed_vault_with_sound(tmp_path)
    vm = LibraryViewModel(vault_root=vault, index_path=tmp_path / "index.sqlite3")
    vm.rebuild_index()
    assert vm.set_user_notes("does-not-exist", "x") is False


# --- transport: relay -> inbox --------------------------------------------


def test_shortcut_inbox_stores_and_reads_note(tmp_path):
    store = ShortcutInboxStore(tmp_path / "inbox.jsonl")
    item = store.add_url("https://example.com/x", source="ios_shortcut", note="  gym intro  ")
    # note is trimmed on the way in
    assert item.note == "gym intro"
    # and survives a re-read from disk
    assert store.all_items()[0].note == "gym intro"


def test_relay_client_carries_note_through_poll_to_inbox(tmp_path):
    def fake_get_json(url, *, params, headers, timeout):
        return {
            "items": [
                {"id": "in_1", "url": "https://example.com/a", "source": "ios_shortcut", "note": "wedding vibes"},
                {"id": "in_2", "url": "https://example.com/b", "source": "ios_shortcut"},
            ]
        }

    client = RelayClient(
        base_url="https://relay.test",
        device_id="d",
        device_secret="s",
        pair_code="PAIR-1",
        get_json=fake_get_json,
    )
    items = client.poll()
    assert [i.note for i in items] == ["wedding vibes", ""]

    inbox = tmp_path / "inbox.jsonl"
    client.poll_to_inbox(inbox)
    by_url = {i.url: i for i in ShortcutInboxStore(inbox).all_items()}
    assert by_url["https://example.com/a"].note == "wedding vibes"
    assert by_url["https://example.com/b"].note == ""
