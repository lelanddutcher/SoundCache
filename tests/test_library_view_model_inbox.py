from sound_vault.ui.view_model import LibraryViewModel


def test_library_view_model_reports_pending_shortcut_count(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    inbox = tmp_path / "shortcut-inbox.jsonl"
    vm = LibraryViewModel(vault_root=vault, index_path=tmp_path / "index.sqlite3", inbox_path=inbox)

    vm.add_shortcut_url("https://www.tiktok.com/t/abc/", source="ios_shortcut", relay_id="in_1")

    assert vm.inbox_text() == "1 pending Shortcut link"
    assert vm.pending_inbox()[0].url == "https://www.tiktok.com/t/abc/"


def test_library_view_model_polls_relay_into_shortcut_inbox(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    inbox = tmp_path / "shortcut-inbox.jsonl"
    calls = []

    def fake_get_json(url, *, params, headers, timeout):
        calls.append((url, params, headers, timeout))
        return {
            "items": [
                {
                    "id": "relay_1",
                    "url": "https://www.tiktok.com/t/relay/",
                    "source": "ios_shortcut",
                }
            ]
        }

    vm = LibraryViewModel(vault_root=vault, index_path=tmp_path / "index.sqlite3", inbox_path=inbox)

    imported = vm.poll_relay_inbox(
        base_url="https://relay.example.test/",
        pair_code="VAULT-ABCD-2345",
        device_id="device-a",
        device_secret="secret-a",
        get_json=fake_get_json,
    )

    assert [item.id for item in imported] == ["relay_1"]
    assert vm.pending_inbox()[0].url == "https://www.tiktok.com/t/relay/"
    assert vm.pending_inbox()[0].relay_id == "relay_1"
    assert calls == [
        (
            "https://relay.example.test/v1/inbox/poll",
            {"pair_code": "VAULT-ABCD-2345"},
            {"x-device-id": "device-a", "x-device-secret": "secret-a"},
            20.0,
        )
    ]
