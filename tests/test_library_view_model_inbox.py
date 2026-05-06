from sound_vault.ui.view_model import LibraryViewModel


def test_library_view_model_reports_pending_shortcut_count(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    inbox = tmp_path / "shortcut-inbox.jsonl"
    vm = LibraryViewModel(vault_root=vault, index_path=tmp_path / "index.sqlite3", inbox_path=inbox)

    vm.add_shortcut_url("https://www.tiktok.com/t/abc/", source="ios_shortcut", relay_id="in_1")

    assert vm.inbox_text() == "1 pending Shortcut link"
    assert vm.pending_inbox()[0].url == "https://www.tiktok.com/t/abc/"
