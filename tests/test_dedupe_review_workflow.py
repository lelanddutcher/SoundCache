import json

from sound_vault.ui.view_model import LibraryViewModel
from sound_vault.workers.dedupe_review import (
    DuplicateDecisionStore,
    DuplicateReviewGroup,
    append_manual_duplicate_group,
    load_duplicate_review_groups,
)


def test_load_duplicate_review_groups_from_existing_report_shape(tmp_path):
    report = tmp_path / "duplicate-candidates.json"
    report.write_text(
        json.dumps(
            [
                {
                    "group_key": "same-title-artist",
                    "score": 0.94,
                    "reason": "normalized title + artist match",
                    "candidates": [
                        {"music_id": "1", "title": "Kickoff", "artist": "Creator", "local_audio_path": "/tmp/1.m4a"},
                        {"music_id": "2", "title": "Kickoff!", "artist": "Creator", "local_audio_path": "/tmp/2.m4a"},
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    groups = load_duplicate_review_groups(report)

    assert groups == [
        DuplicateReviewGroup(
            group_id="same-title-artist",
            score=0.94,
            reason="normalized title + artist match",
            candidates=(
                {"music_id": "1", "title": "Kickoff", "artist": "Creator", "local_audio_path": "/tmp/1.m4a"},
                {"music_id": "2", "title": "Kickoff!", "artist": "Creator", "local_audio_path": "/tmp/2.m4a"},
            ),
        )
    ]


def test_duplicate_decision_store_records_human_review_without_touching_audio(tmp_path):
    decisions = DuplicateDecisionStore(tmp_path / "duplicate-decisions.jsonl")

    decisions.record_decision(
        group_id="same-title-artist",
        decision="duplicates",
        keep_music_id="1",
        duplicate_music_ids=["2"],
        notes="same waveform, keep cleaner title",
    )

    [row] = decisions.read_decisions()
    assert row["group_id"] == "same-title-artist"
    assert row["decision"] == "duplicates"
    assert row["keep_music_id"] == "1"
    assert row["duplicate_music_ids"] == ["2"]
    assert row["notes"] == "same waveform, keep cleaner title"
    assert "decided_at" in row


def test_append_manual_duplicate_group_adds_reviewable_group_without_decision(tmp_path):
    report = tmp_path / "duplicate-candidates.json"
    report.write_text(
        json.dumps(
            [
                {
                    "group_key": "existing",
                    "candidates": [{"music_id": "old-1"}, {"music_id": "old-2"}],
                }
            ]
        ),
        encoding="utf-8",
    )

    group = append_manual_duplicate_group(
        report,
        [
            {"music_id": "1", "title": "Keeper?", "artist": "Creator", "folder": "/tmp/one"},
            {"music_id": "2", "title": "Duplicate?", "artist": "Creator", "folder": "/tmp/two"},
        ],
    )

    groups = load_duplicate_review_groups(report)
    assert group.group_id.startswith("manual-")
    assert [item.group_id for item in groups] == ["existing", group.group_id]
    assert groups[-1].reason == "Manual duplicate group from Library selection."
    assert [candidate["music_id"] for candidate in groups[-1].candidates] == ["1", "2"]


def test_duplicate_review_playback_resolves_audio_from_indexed_sound_record(tmp_path):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    sound_dir = vault / "sounds" / "1 - Kickoff"
    reports = vault / "reports"
    catalog.mkdir(parents=True)
    sound_dir.mkdir(parents=True)
    reports.mkdir()
    audio = sound_dir / "kickoff.m4a"
    audio.write_bytes(b"audio")
    (sound_dir / "metadata.json").write_text(
        json.dumps(
            {
                "tiktok_music_id": "1",
                "tiktok_visible_title": "Kickoff",
                "paths": {"folder": str(sound_dir), "audio": str(audio)},
            }
        ),
        encoding="utf-8",
    )
    (catalog / "sounds.jsonl").write_text(
        json.dumps({"tiktok_music_id": "1", "tiktok_visible_title": "Kickoff", "paths": {"folder": str(sound_dir)}})
        + "\n",
        encoding="utf-8",
    )
    (reports / "duplicate-candidates.json").write_text(
        json.dumps(
            [
                {
                    "group_key": "kickoff",
                    "candidates": [
                        {"music_id": "1", "title": "Kickoff"},
                        {"music_id": "2", "title": "Kickoff Copy"},
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    vm = LibraryViewModel(vault_root=vault, index_path=tmp_path / "index.sqlite3", load_sidecars=False, sidecar_mode="summary")
    vm.rebuild_index()

    [group] = vm.duplicate_review_groups()

    assert vm.duplicate_candidate_play_target(group.candidates[0]) == audio


def test_duplicate_review_groups_hide_reviewed_terminal_decisions(tmp_path):
    vault = tmp_path / "vault"
    reports = vault / "reports"
    reports.mkdir(parents=True)
    (reports / "duplicate-candidates.json").write_text(
        json.dumps(
            [
                {
                    "group_key": "already-reviewed",
                    "candidates": [{"music_id": "1"}, {"music_id": "2"}],
                },
                {
                    "group_key": "still-open",
                    "candidates": [{"music_id": "3"}, {"music_id": "4"}],
                },
            ]
        ),
        encoding="utf-8",
    )
    vm = LibraryViewModel(vault_root=vault, index_path=tmp_path / "index.sqlite3")

    vm.record_duplicate_decision(group_id="already-reviewed", decision="not_duplicates")

    assert [group.group_id for group in vm.duplicate_review_groups()] == ["still-open"]


def test_duplicate_candidate_preview_hydrates_indexed_metadata(tmp_path):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    sound_dir = vault / "sounds" / "1 - Kickoff"
    reports = vault / "reports"
    catalog.mkdir(parents=True)
    sound_dir.mkdir(parents=True)
    reports.mkdir()
    audio = sound_dir / "kickoff.m4a"
    audio.write_bytes(b"audio")
    (sound_dir / "transcript.json").write_text(json.dumps({"text": "lyric line", "language": "en"}), encoding="utf-8")
    (sound_dir / "metadata.json").write_text(
        json.dumps(
            {
                "tiktok_music_id": "1",
                "tiktok_visible_title": "Kickoff",
                "source_artist": "Creator",
                "duration_seconds": 7,
                "paths": {"folder": str(sound_dir), "audio": str(audio)},
            }
        ),
        encoding="utf-8",
    )
    (catalog / "sounds.jsonl").write_text(
        json.dumps({"tiktok_music_id": "1", "tiktok_visible_title": "Kickoff", "paths": {"folder": str(sound_dir)}})
        + "\n",
        encoding="utf-8",
    )
    vm = LibraryViewModel(vault_root=vault, index_path=tmp_path / "index.sqlite3", load_sidecars=False, sidecar_mode="summary")
    vm.rebuild_index()

    record = vm.duplicate_candidate_preview({"music_id": "1", "title": "wrong stale report title"})

    assert record is not None
    assert record.title == "Kickoff"
    assert record.artist == "Creator"
    assert record.duration_seconds == 7
    assert record.transcript_text == "lyric line"


def test_duplicate_candidate_preview_uses_report_artwork_and_transcript_fallback(tmp_path):
    vault = tmp_path / "vault"
    folder = vault / "sounds" / "missing-index"
    reports = vault / "reports"
    folder.mkdir(parents=True)
    reports.mkdir(parents=True)
    artwork = folder / "artwork.jpg"
    audio = folder / "sound.m4a"
    artwork.write_bytes(b"jpg")
    audio.write_bytes(b"audio")
    vm = LibraryViewModel(vault_root=vault, index_path=tmp_path / "index.sqlite3")

    record = vm.duplicate_candidate_preview(
        {
            "music_id": "not-indexed",
            "title": "Report Only",
            "artist": "Creator",
            "folder": str(folder),
            "local_audio_path": str(audio),
            "artwork_path": str(artwork),
            "transcript_excerpt": "fallback lyric phrase",
            "duration_seconds": 4.2,
        }
    )

    assert record is not None
    assert record.title == "Report Only"
    assert record.artwork_path == artwork
    assert record.local_audio_path == audio
    assert record.transcript_text == "fallback lyric phrase"
    assert record.duration_seconds == 4.2


def test_view_model_creates_manual_duplicate_group_from_indexed_library_selection(tmp_path):
    vault = tmp_path / "vault"
    catalog = vault / "catalog"
    catalog.mkdir(parents=True)
    _write_manual_duplicate_fixture_sound(vault, catalog, "1", "First Hook", "Creator")
    _write_manual_duplicate_fixture_sound(vault, catalog, "2", "Second Hook", "Creator")
    vm = LibraryViewModel(vault_root=vault, index_path=tmp_path / "index.sqlite3", load_sidecars=False, sidecar_mode="summary")
    vm.rebuild_index()

    group = vm.create_manual_duplicate_group(["1", "2"])

    [review_group] = vm.duplicate_review_groups()
    assert review_group.group_id == group.group_id
    assert group.candidates[0]["title"] == "First Hook"
    assert group.candidates[0]["transcript_excerpt"] == "First Hook transcript"
    assert (vault / "reports" / "duplicate-candidates.json").exists()


def test_quarantine_duplicate_candidates_moves_duplicate_folder_and_records_decision(tmp_path):
    vault = tmp_path / "vault"
    reports = vault / "reports"
    keep = vault / "sounds" / "1 - Keep"
    duplicate = vault / "sounds" / "2 - Duplicate"
    reports.mkdir(parents=True)
    keep.mkdir(parents=True)
    duplicate.mkdir(parents=True)
    (keep / "metadata.json").write_text(json.dumps({"tiktok_music_id": "1"}), encoding="utf-8")
    (duplicate / "metadata.json").write_text(json.dumps({"tiktok_music_id": "2"}), encoding="utf-8")
    (reports / "duplicate-candidates.json").write_text(
        json.dumps(
            [
                {
                    "group_key": "same-title",
                    "candidates": [
                        {"music_id": "1", "title": "Keep", "folder": str(keep)},
                        {"music_id": "2", "title": "Duplicate", "folder": str(duplicate)},
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    vm = LibraryViewModel(vault_root=vault, index_path=tmp_path / "index.sqlite3")

    result = vm.quarantine_duplicate_candidates(group_id="same-title", keep_music_id="1", duplicate_music_ids=["2"])

    assert keep.exists()
    assert not duplicate.exists()
    assert result["moved"][0]["music_id"] == "2"
    moved_to = result["moved"][0]["to"]
    assert "duplicate-quarantine" in moved_to
    decisions = vm.duplicate_decisions.read_decisions()
    assert decisions[-1]["decision"] == "quarantined_duplicates"
    assert decisions[-1]["keep_music_id"] == "1"
    assert decisions[-1]["duplicate_music_ids"] == ["2"]


def _write_manual_duplicate_fixture_sound(vault, catalog, music_id: str, title: str, artist: str) -> None:
    sound_dir = vault / "sounds" / f"{music_id} - {title}"
    sound_dir.mkdir(parents=True)
    audio = sound_dir / f"{music_id}.m4a"
    audio.write_bytes(b"audio")
    (sound_dir / "transcript.json").write_text(json.dumps({"text": f"{title} transcript"}), encoding="utf-8")
    (sound_dir / "metadata.json").write_text(
        json.dumps(
            {
                "tiktok_music_id": music_id,
                "tiktok_visible_title": title,
                "source_artist": artist,
                "paths": {"folder": str(sound_dir), "audio": str(audio)},
            }
        ),
        encoding="utf-8",
    )
    with (catalog / "sounds.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "tiktok_music_id": music_id,
                    "tiktok_visible_title": title,
                    "source_artist": artist,
                    "paths": {"folder": str(sound_dir)},
                }
            )
            + "\n"
        )
