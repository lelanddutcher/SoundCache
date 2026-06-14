from sound_vault.ingest.package import package_sound
from sound_vault.vault.indexer import build_index
from sound_vault.workers.dedupe_actions import DedupeActionResult, DedupeService


def _seed(tmp_path):
    package_sound(vault_root=tmp_path, music_id="1", title="Keep", artist="A", audio_path=None, now_iso="t")
    package_sound(vault_root=tmp_path, music_id="2", title="Dup", artist="A", audio_path=None, now_iso="t")


def test_apply_decision_archives_duplicate(tmp_path):
    _seed(tmp_path)
    res = DedupeService(tmp_path).apply_decision(keep_music_id="1", duplicate_music_ids=["2"], now="t")
    assert isinstance(res, DedupeActionResult)
    assert res.archived == ["2"]
    assert not (tmp_path / "sounds" / "2 - Dup - A").exists()
    assert (tmp_path / "archive" / "dedupe" / "2 - Dup - A").exists()
    assert {r.music_id for r in build_index(tmp_path)} == {"1"}
    assert (tmp_path / "reports" / "dedupe-undo.jsonl").exists()


def test_dry_run_changes_nothing(tmp_path):
    _seed(tmp_path)
    res = DedupeService(tmp_path).apply_decision(keep_music_id="1", duplicate_music_ids=["2"], dry_run=True)
    assert res.dry_run is True
    assert res.archived == ["2"]
    assert (tmp_path / "sounds" / "2 - Dup - A").exists()
    assert {r.music_id for r in build_index(tmp_path)} == {"1", "2"}


def test_skips_missing_duplicate(tmp_path):
    _seed(tmp_path)
    res = DedupeService(tmp_path).apply_decision(keep_music_id="1", duplicate_music_ids=["999"])
    assert res.skipped == ["999"]
    assert res.archived == []


def test_never_archives_the_keeper(tmp_path):
    _seed(tmp_path)
    res = DedupeService(tmp_path).apply_decision(keep_music_id="1", duplicate_music_ids=["1", "2"])
    assert "1" not in res.archived
    assert (tmp_path / "sounds" / "1 - Keep - A").exists()


def test_undo_restores_folder_and_catalog(tmp_path):
    _seed(tmp_path)
    svc = DedupeService(tmp_path)
    res = svc.apply_decision(keep_music_id="1", duplicate_music_ids=["2"], now="t")
    svc.undo(res.undo_entries[0])
    assert (tmp_path / "sounds" / "2 - Dup - A").exists()
    assert {r.music_id for r in build_index(tmp_path)} == {"1", "2"}


def test_apply_recorded_decisions(tmp_path):
    from sound_vault.workers.dedupe_review import DuplicateDecisionStore

    _seed(tmp_path)
    store = DuplicateDecisionStore(tmp_path / "reports" / "duplicate-decisions.jsonl")
    store.record_decision(group_id="g", decision="duplicates", keep_music_id="1", duplicate_music_ids=["2"])
    store.record_decision(group_id="g2", decision="keep_all", keep_music_id="", duplicate_music_ids=[])
    results = DedupeService(tmp_path).apply_recorded_decisions(store)
    archived = [mid for r in results for mid in r.archived]
    assert archived == ["2"]
    assert {r.music_id for r in build_index(tmp_path)} == {"1"}
