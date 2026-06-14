from pathlib import Path

DESKTOP_SOURCE = Path("src/sound_vault/ui/desktop.py")
VIEW_MODEL_SOURCE = Path("src/sound_vault/ui/view_model.py")


def test_desktop_has_dedupe_review_tab_with_play_and_human_decision_controls():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")
    vm_source = VIEW_MODEL_SOURCE.read_text(encoding="utf-8")

    assert '("Duplicate review", "dedupe")' in source
    assert "self.dedupe_view = self._build_dedupe_view()" in source
    assert "refresh_dedupe_review" in source
    assert "self.dedupe_groups_table" in source
    assert "self.dedupe_candidates_table" in source
    assert "Mark duplicates" in source
    assert "Mark not duplicates" in source
    assert "Quarantine duplicates" in source
    assert "quarantine_selected_duplicates" in source
    assert "update_preview_from_dedupe_selection" in source
    assert "duplicate_candidate_preview" in vm_source
    assert "removed group from queue" in source
    assert "play_dedupe_candidate" in source
    assert "duplicate_candidate_play_target" in vm_source
    assert "_reviewed_duplicate_group_ids" in vm_source
    assert "quarantine_duplicate_candidates" in vm_source
    assert "record_duplicate_decision" in vm_source
    assert "load_duplicate_review_groups" in vm_source
    assert "Mark as Duplicate" in source
    assert "mark_selected_library_as_duplicate" in source
    assert "create_manual_duplicate_group" in vm_source
    assert "append_manual_duplicate_group" in vm_source
    assert "selected row is the keeper" in source.lower()
    assert "reports/duplicate-quarantine" in source
