from sound_vault.db.index_db import IndexDatabase
from sound_vault.vault.indexer import SoundRecord


def rec(music_id, *, title="T", artist="A", status="ingested", tags=("from_shortcut",)):
    return SoundRecord(music_id=music_id, title=title, artist=artist, tags=tuple(tags), status=status, raw={})


def test_upsert_adds_to_empty_cache(tmp_path):
    db = IndexDatabase(tmp_path / "index.sqlite3")
    db.upsert(rec("123", title="Kickoff"))
    got = db.get("123")
    assert got is not None and got.title == "Kickoff"
    assert db.stats().total_sounds == 1
    assert [r.music_id for r in db.search("kickoff")] == ["123"]


def test_upsert_updates_existing_without_duplicating(tmp_path):
    db = IndexDatabase(tmp_path / "index.sqlite3")
    db.upsert(rec("123", status="ingested"))
    db.upsert(rec("123", status="approved", title="Renamed"))
    assert db.stats().total_sounds == 1
    got = db.get("123")
    assert got.status == "approved"
    assert got.title == "Renamed"


def test_upsert_preserves_other_rows(tmp_path):
    db = IndexDatabase(tmp_path / "index.sqlite3")
    db.rebuild([rec("1"), rec("2")])
    db.upsert(rec("3", title="Third"))
    assert db.stats().total_sounds == 3
    assert db.get("1") is not None
    assert db.get("3").title == "Third"


def test_upsert_many(tmp_path):
    db = IndexDatabase(tmp_path / "index.sqlite3")
    db.upsert_many([rec("a"), rec("b"), rec("a", title="A-again")])
    assert db.stats().total_sounds == 2
    assert db.get("a").title == "A-again"
