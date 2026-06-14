from pathlib import Path

from sound_vault.db.index_db import IndexDatabase
from sound_vault.vault.indexer import AssociatedVideo, SoundRecord


def _record():
    return SoundRecord(
        music_id="1",
        title="T",
        artist="A",
        tags=("from_shortcut",),
        status="ingested",
        raw={"k": "v", "paths": {"folder": "sounds/1 - T - A", "audio": "sounds/1 - T - A/x.m4a"}},
        evidence_images=(Path("/tmp/a.jpg"), Path("/tmp/b.jpg")),
        associated_videos=(
            AssociatedVideo(rank=1, video_id="v1", author_handle="@x", video_url="u1", description="d1"),
            AssociatedVideo(rank=2, video_id="v2", author_handle="@y", video_url="u2", description="d2",
                            screenshot_path=Path("/tmp/s2.jpg")),
        ),
    )


def test_get_preserves_raw_evidence_and_videos(tmp_path):
    db = IndexDatabase(tmp_path / "i.sqlite3")
    db.upsert(_record())
    got = db.get("1")
    assert got is not None
    assert got.raw == {"k": "v", "paths": {"folder": "sounds/1 - T - A", "audio": "sounds/1 - T - A/x.m4a"}}
    assert [str(p) for p in got.evidence_images] == ["/tmp/a.jpg", "/tmp/b.jpg"]
    assert len(got.associated_videos) == 2
    assert got.associated_videos[0].video_id == "v1"
    assert got.associated_videos[0].rank == 1
    assert got.associated_videos[1].screenshot_path == Path("/tmp/s2.jpg")


def test_search_preserves_raw(tmp_path):
    db = IndexDatabase(tmp_path / "i.sqlite3")
    db.upsert(_record())
    results = db.search("t")
    assert results
    assert results[0].raw.get("k") == "v"
    assert results[0].associated_videos[0].video_id == "v1"


def test_rebuild_then_read_roundtrips(tmp_path):
    db = IndexDatabase(tmp_path / "i.sqlite3")
    db.rebuild([_record()])
    got = db.get("1")
    assert got.raw.get("k") == "v"
    assert [str(p) for p in got.evidence_images] == ["/tmp/a.jpg", "/tmp/b.jpg"]


def test_empty_extras_default_safely(tmp_path):
    db = IndexDatabase(tmp_path / "i.sqlite3")
    db.upsert(SoundRecord(music_id="2", title="T2", artist="A2", tags=(), status="ingested", raw={}))
    got = db.get("2")
    assert got.raw == {}
    assert got.evidence_images == ()
    assert got.associated_videos == ()
