import json

from sound_vault.workers.dedupe_review import (
    DuplicateDecisionStore,
    DuplicateReviewGroup,
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
