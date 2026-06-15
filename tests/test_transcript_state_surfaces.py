"""The 4-state transcript model is consistent across filters, health, and copy.

Covers the DB media filters + archive-health split, plus the view-model surfaces
(review queue, copyable metadata) — all driven from the same indexer classifier.
"""
from __future__ import annotations

from sound_vault.db.index_db import IndexDatabase
from sound_vault.ui.view_model import LibraryViewModel
from sound_vault.vault.indexer import SoundRecord


def _rec(mid: str, **kw) -> SoundRecord:
    base = dict(music_id=mid, title="T", artist="A", tags=(), status="approved", raw={})
    base.update(kw)
    return SoundRecord(**base)


def _four_states(tmp_path):
    return [
        _rec("avail", transcript_text="hello there friend"),
        _rec("inst", transcript_text="", transcript_path=tmp_path / "t.json", local_audio_path=tmp_path / "a.m4a"),
        _rec("pend", transcript_text="", transcript_path=None, local_audio_path=tmp_path / "b.m4a"),
        _rec("noaud", transcript_text="", transcript_path=None, local_audio_path=None),
    ]


def test_index_db_empty_and_pending_filters(tmp_path):
    db = IndexDatabase(tmp_path / "i.sqlite3")
    db.rebuild(_four_states(tmp_path))

    assert {r.music_id for r in db.search("", media_filter="empty_transcript")} == {"inst"}
    assert {r.music_id for r in db.search("", media_filter="pending_transcript")} == {"pend"}
    assert {r.music_id for r in db.search("", media_filter="has_transcript")} == {"avail"}
    # the coarse "missing" filter still covers all three text-empty states
    assert {r.music_id for r in db.search("", media_filter="missing_transcript")} == {"inst", "pend", "noaud"}


def test_archive_health_counts_split_empty_vs_pending(tmp_path):
    db = IndexDatabase(tmp_path / "i.sqlite3")
    db.rebuild(_four_states(tmp_path))

    h = db.archive_health_counts()
    assert h["empty_transcript"] == 1  # inst
    assert h["pending_transcript"] == 1  # pend
    assert h["missing_transcript"] == 3  # inst + pend + noaud (backward-compatible total)


def test_review_queue_flags_pending_but_not_instrumental(tmp_path):
    vm = LibraryViewModel(vault_root=tmp_path / "vault", index_path=tmp_path / "i.sqlite3")

    # Only an instrumental -> nothing actionable about its (absent) transcript.
    vm.db.rebuild([_rec("inst", transcript_text="", transcript_path=tmp_path / "t.json", local_audio_path=tmp_path / "a.m4a")])
    assert "Not transcribed yet" not in [r[0] for r in vm.review_queue_rows()]

    # A sound with audio but no transcript -> actionable, links to pending filter.
    vm.db.rebuild([_rec("pend", transcript_text="", transcript_path=None, local_audio_path=tmp_path / "b.m4a")])
    assert ("Not transcribed yet", 1, "Run local ASR sidecar worker", "all", "pending_transcript") in vm.review_queue_rows()


def test_copyable_metadata_reports_specific_transcript_state(tmp_path):
    vm = LibraryViewModel(vault_root=tmp_path / "vault", index_path=tmp_path / "i.sqlite3")

    pend = _rec("p", transcript_text="", transcript_path=None, local_audio_path=tmp_path / "a.m4a")
    assert "transcript not run yet" in vm.copyable_metadata(pend)

    sidecar = tmp_path / "t.json"
    sidecar.write_text("{}", encoding="utf-8")
    inst = _rec("i", transcript_text="", transcript_path=sidecar, local_audio_path=tmp_path / "a.m4a")
    assert "instrumental (no speech)" in vm.copyable_metadata(inst)
