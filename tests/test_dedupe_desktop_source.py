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
    assert "play_dedupe_candidate" in source
    assert "record_duplicate_decision" in vm_source
    assert "load_duplicate_review_groups" in vm_source
